"""
Targeted Memory Vision Capture & Diff Analysis (Production Hardened)
- Captures strictly the DWM-visible target window rectangle in memory (Zero Disk I/O).
- Clips capture region against virtual desktop bounds to prevent mss off-screen exceptions,
  while padding clipped edges so image coordinates remain 1:1 with window-relative coordinates.
- Fallback to Win32 PrintWindow / GDI BitBlt if DXGI/mss fails.
- Provides fast mean + perceptual grid diffing to detect subtle UI state transitions.
"""

import ctypes
from io import BytesIO
from typing import Optional, Tuple, Dict, Any

import mss
import win32con
import win32gui
import win32ui
from PIL import Image, ImageChops, ImageDraw, ImageStat

from .binder import WindowBinder, WindowRect


class WindowVision:
    def __init__(self, binder: WindowBinder):
        self.binder = binder
        self._sct: Optional[mss.mss] = None
        self.last_capture: Optional[Image.Image] = None
        self.last_rect: Optional[WindowRect] = None

    @property
    def sct(self) -> mss.mss:
        if self._sct is None:
            self._sct = mss.mss()
        return self._sct

    def _get_virtual_screen_bounds(self) -> WindowRect:
        """Return virtual screen bounding box across all monitors."""
        v_mon = self.sct.monitors[0]
        return WindowRect(
            left=v_mon["left"],
            top=v_mon["top"],
            right=v_mon["left"] + v_mon["width"],
            bottom=v_mon["top"] + v_mon["height"],
        )

    def _capture_via_gdi_printwindow(self, rect: WindowRect) -> Optional[Image.Image]:
        """Fallback in-memory capture via Win32 PrintWindow / BitBlt if mss fails."""
        hwnd = self.binder.ensure_alive()
        width, height = rect.width, rect.height
        if width <= 0 or height <= 0:
            return None

        hwnd_dc = None
        mfc_dc = None
        save_dc = None
        save_bitmap = None
        try:
            hwnd_dc = win32gui.GetWindowDC(hwnd)
            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()
            save_bitmap = win32ui.CreateBitmap()
            save_bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
            save_dc.SelectObject(save_bitmap)

            # PW_RENDERFULLCONTENT = 0x00000002 (works with DirectComposition / hardware accelerated apps)
            res = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)
            if res == 0:
                save_dc.BitBlt((0, 0), (width, height), mfc_dc, (0, 0), win32con.SRCCOPY)

            bmpinfo = save_bitmap.GetInfo()
            bmpstr = save_bitmap.GetBitmapBits(True)
            img = Image.frombuffer(
                "RGB",
                (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                bmpstr,
                "raw",
                "BGRX",
                0,
                1,
            )
            return img.copy()
        except Exception:
            return None
        finally:
            if save_bitmap is not None:
                try:
                    win32gui.DeleteObject(save_bitmap.GetHandle())
                except Exception:
                    pass
            if save_dc is not None:
                try:
                    save_dc.DeleteDC()
                except Exception:
                    pass
            if mfc_dc is not None:
                try:
                    mfc_dc.DeleteDC()
                except Exception:
                    pass
            if hwnd_dc is not None:
                try:
                    win32gui.ReleaseDC(hwnd, hwnd_dc)
                except Exception:
                    pass

    def capture(self, ensure_foreground: bool = False) -> Image.Image:
        """
        Capture the target window rectangle into an in-memory PIL.Image (RGB).
        Preserves exact 1:1 pixel alignment with WindowBinder relative coordinates
        even if the window is partially moved off-screen.
        """
        if ensure_foreground:
            rect = self.binder.focus()
        else:
            rect = self.binder.update_rect()

        if rect.width <= 0 or rect.height <= 0:
            raise RuntimeError(f"Invalid target window dimensions: {rect.width}x{rect.height}")

        v_bounds = self._get_virtual_screen_bounds()

        # Intersect target rect with virtual desktop bounds for safe mss grab
        clip_left = max(rect.left, v_bounds.left)
        clip_top = max(rect.top, v_bounds.top)
        clip_right = min(rect.right, v_bounds.right)
        clip_bottom = min(rect.bottom, v_bounds.bottom)

        clip_width = clip_right - clip_left
        clip_height = clip_bottom - clip_top

        img: Optional[Image.Image] = None

        if clip_width > 0 and clip_height > 0:
            monitor = {
                "left": int(clip_left),
                "top": int(clip_top),
                "width": int(clip_width),
                "height": int(clip_height),
            }
            try:
                sct_img = self.sct.grab(monitor)
                grabbed = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

                if clip_width == rect.width and clip_height == rect.height:
                    img = grabbed
                else:
                    # Reconstruct full window canvas so (rel_x, rel_y) stays strictly aligned
                    canvas = Image.new("RGB", (rect.width, rect.height), (0, 0, 0))
                    paste_x = clip_left - rect.left
                    paste_y = clip_top - rect.top
                    canvas.paste(grabbed, (paste_x, paste_y))
                    img = canvas
            except Exception:
                # Reset mss instance on thread/display mode switch and try GDI fallback
                self.close()
                img = self._capture_via_gdi_printwindow(rect)
        else:
            img = self._capture_via_gdi_printwindow(rect)

        if img is None:
            raise RuntimeError("Failed to capture target window via both mss and GDI fallback.")

        self.last_capture = img
        self.last_rect = rect
        return img

    def to_png_bytes(self, img: Optional[Image.Image] = None) -> bytes:
        """Encode PIL Image to PNG bytes in memory without touching disk."""
        target_img = img or self.capture()
        buf = BytesIO()
        target_img.save(buf, format="PNG", optimize=False)
        return buf.getvalue()

    def calculate_diff_metrics(
        self,
        current: Image.Image,
        previous: Optional[Image.Image] = None,
        pixel_threshold: int = 12,
    ) -> Dict[str, Any]:
        """
        Compute detailed difference metrics between two captures:
        - mean_diff_ratio: global average intensity shift (0.0 to 1.0)
        - changed_pixel_ratio: fraction of pixels whose RGB delta exceeds pixel_threshold
        - changed_bbox: bounding box (left, top, right, bottom) of altered UI area
        - ui_changed: boolean indicator if meaningful visual change occurred
        """
        prev = previous or self.last_capture
        if prev is None or current.size != prev.size:
            return {
                "mean_diff_ratio": 1.0,
                "changed_pixel_ratio": 1.0,
                "changed_bbox": (0, 0, current.width, current.height),
                "ui_changed": True,
            }

        diff = ImageChops.difference(current, prev)
        stat = ImageStat.Stat(diff)
        mean_diff_ratio = sum(stat.mean) / (3.0 * 255.0)

        # Convert to grayscale and threshold to detect localized UI changes (e.g., menu popup, hover, cursor text)
        gray_diff = diff.convert("L")
        hist = gray_diff.histogram()
        total_pixels = max(1, current.width * current.height)
        changed_pixels = sum(hist[pixel_threshold:])
        changed_pixel_ratio = changed_pixels / float(total_pixels)

        mask = gray_diff.point(lambda p: 255 if p >= pixel_threshold else 0)
        bbox = mask.getbbox()

        # Even a small context menu or button highlight (>= 0.05% of screen pixels) counts as a UI response
        ui_changed = changed_pixel_ratio >= 0.0005 or mean_diff_ratio >= 0.001

        return {
            "mean_diff_ratio": round(mean_diff_ratio, 6),
            "changed_pixel_ratio": round(changed_pixel_ratio, 6),
            "changed_bbox": bbox,
            "ui_changed": ui_changed,
        }

    def calculate_diff_ratio(
        self, current: Image.Image, previous: Optional[Image.Image] = None
    ) -> float:
        """Backwards-compatible scalar diff ratio (0.0 to 1.0)."""
        metrics = self.calculate_diff_metrics(current, previous)
        return max(metrics["mean_diff_ratio"], metrics["changed_pixel_ratio"])

    def annotate_crosshair(
        self, img: Image.Image, rel_x: int, rel_y: int, radius: int = 12
    ) -> Image.Image:
        """Return a copy of the image with a visual crosshair drawn at (rel_x, rel_y) in memory."""
        annotated = img.copy()
        draw = ImageDraw.Draw(annotated)
        draw.ellipse(
            (rel_x - radius, rel_y - radius, rel_x + radius, rel_y + radius),
            outline=(255, 40, 40),
            width=2,
        )
        draw.line((rel_x - radius - 4, rel_y, rel_x + radius + 4, rel_y), fill=(255, 40, 40), width=2)
        draw.line((rel_x, rel_y - radius - 4, rel_x, rel_y + radius + 4), fill=(255, 40, 40), width=2)
        return annotated

    def capture_cursor_region(
        self,
        box_size: int = 256,
        full_img: Optional[Image.Image] = None,
    ) -> Image.Image:
        """
        Capture or crop a `box_size` x `box_size` region centered around the current mouse cursor.
        Coordinates are strictly aligned with WindowBinder relative coordinates.
        If the cursor or bounding box extends outside the window, edges are clamped/padded.
        """
        rect = self.binder.update_rect()
        cur_x, cur_y = win32gui.GetCursorPos()

        # Window-relative cursor position
        rel_x = cur_x - rect.left
        rel_y = cur_y - rect.top

        half = box_size // 2
        box_left = rel_x - half
        box_top = rel_y - half
        box_right = box_left + box_size
        box_bottom = box_top + box_size

        img = full_img if full_img is not None else self.capture()

        # Create canvas for strict 256x256 output (black padding if outside window boundary)
        out_crop = Image.new("RGB", (box_size, box_size), (0, 0, 0))

        # Intersection with window image bounds
        src_left = max(0, box_left)
        src_top = max(0, box_top)
        src_right = min(img.width, box_right)
        src_bottom = min(img.height, box_bottom)

        if src_right > src_left and src_bottom > src_top:
            cropped = img.crop((src_left, src_top, src_right, src_bottom))
            dst_x = src_left - box_left
            dst_y = src_top - box_top
            out_crop.paste(cropped, (dst_x, dst_y))

        return out_crop

    def capture_burst(
        self,
        duration: float = 1.5,
        fps: int = 15,
        mode: str = "full",  # "full" or "uicheck" (cursor 256x256)
        uicheck_size: int = 256,
    ) -> List[Image.Image]:
        """
        Capture a sequential burst of frames at the specified FPS (drift-compensated).
        - mode="full": Captures the entire target window.
        - mode="uicheck": Captures cursor-following `uicheck_size` x `uicheck_size` crop.
        Returns a list of in-memory PIL.Image frames. Zero disk I/O.
        """
        import time

        frames: List[Image.Image] = []
        interval = 1.0 / float(fps)
        total_frames = max(1, int(round(duration * fps)))

        start_time = time.perf_counter()
        next_target = start_time

        for _ in range(total_frames):
            if mode == "uicheck":
                frame = self.capture_cursor_region(box_size=uicheck_size)
            else:
                frame = self.capture()
            frames.append(frame)

            next_target += interval
            now = time.perf_counter()
            sleep_sec = next_target - now
            if sleep_sec > 0:
                time.sleep(sleep_sec)

        return frames

    def build_animated_bytes(
        self,
        frames: List[Image.Image],
        fps: int = 15,
        format: str = "WEBP",  # "WEBP" or "GIF"
    ) -> bytes:
        """
        Encode a sequence of frames into an in-memory animated WebP/GIF. Zero disk I/O.
        """
        if not frames:
            return b""
        duration_ms = int(round(1000.0 / float(fps)))
        buf = BytesIO()
        first_frame = frames[0]
        other_frames = frames[1:] if len(frames) > 1 else []

        if format.upper() == "WEBP":
            first_frame.save(
                buf,
                format="WEBP",
                save_all=True,
                append_images=other_frames,
                duration=duration_ms,
                loop=0,
                quality=85,
                method=4,
            )
        elif format.upper() == "GIF":
            first_frame.save(
                buf,
                format="GIF",
                save_all=True,
                append_images=other_frames,
                duration=duration_ms,
                loop=0,
            )
        else:
            raise ValueError(f"Unsupported animation format: {format}")

        return buf.getvalue()

    def close(self) -> None:
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None

    def __enter__(self) -> "WindowVision":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
