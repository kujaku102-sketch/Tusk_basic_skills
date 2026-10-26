---
name: gamerseye
description: Target-Bound Autonomous Vision & ComputerUse Agent for Windows GUI applications (Tuskfolio, InDesign, Unity Editor, etc.) using Per-Monitor DPI V2, DWM invisible border stripping, Win32 SendInput, and Gemini/Claude multimodal models.
---

# GamersEYE (`/gamerseye`)
## 汎用ターゲット拘束型 ComputerUse ＆ リアルタイム視覚エージェント

GamersEYE は、Windows デスクトップ上の任意のGUIアプリケーション（Tuskfolio / `act3_view.exe`、InDesign、Unity Editor、ゲーム等）を **PID または ウィンドウ名正規表現** で捕捉し、安全に自律視覚・操作・UI検証を行うエージェントSkillです。
本フォルダ（`.agents/skills/gamerseye/`）単体で完全自己完結しており、他の環境やマシンへそのままコピーして動作します。

- **設計書**: `Tusk_architecture/docs/SPEC-UNIVERSAL-COMPUTER-USE-001.md`
- **自己完結モジュール**: [gamerseye_core/](gamerseye_core/)
- **スタンドアロンCLI**: [run.py](run.py)
- **依存一覧**: [requirements.txt](requirements.txt)
- **セキュリティ・アーキテクチャ**:
  1. **Per-Monitor DPI Awareness V2**: 125%/150% 等の拡大ディスプレイでも1ピクセルも狂わない物理座標系。
  2. **DWM 拡張枠 (DWMWA_EXTENDED_FRAME_BOUNDS)**: Windows 10/11 の不可視8pxシャドウ枠を完全除外し、ウィンドウ外やデスクトップへの誤爆をOSレイヤーで物理遮断。
  3. **相対座標 (0..1000 または rel_x, rel_y) クランプ**: 操作対象ウィンドウの枠内のみに入力を強制制限。
  4. **Win32 SendInput (Unicode対応)**: 日本語入力・記号・ショートカットキーの完全対応。
  5. **ゼロディスク即時視覚**: メモリ上（mss / Pillow）でウィンドウ矩形を瞬発クロップし、LLMに高速供給。
  6. **APIキー自動フォールバック**: 環境変数 `GEMINI_API_KEY` / `GOOGLE_API_KEY` に加え、`work/settings/gemini_api_key.txt` からも自動検出。

---

## 使い方 (Quick Start)

### 1. ターゲットウィンドウの吸着テスト (Dry-Run / 座標・矩形検証)
ターゲットアプリ（例: Tuskfolio）を起動した状態で、ウィンドウを捕捉して可視矩形とキャプチャが正確か確認します（操作は行われません）。

```powershell
python .agents/skills/gamerseye/run.py --title "TuskFolio" --dry-run
```

PIDで指定する場合:
```powershell
python .agents/skills/gamerseye/run.py --pid <PID> --dry-run
```

### 2. 自律操作 ＆ UI操作マトリクス生成
Gemini 4 Argon / Flash などのマルチモーダルモデルを用いて自律的にUIを巡回し、操作経路マップ (`UI_OPERATION_MATRIX.md`) を作成させます。

```powershell
python .agents/skills/gamerseye/run.py --title "TuskFolio" --goal "全メニューを走査してUI操作マトリクスを作成せよ" --max-steps 15
```

---

## 主な引数オプション
- `--title <REGEX>`: ウィンドウタイトルの正規表現（例: `"TuskFolio"`, `"InDesign"`）
- `--pid <PID>`: プロセスID（直接指定）
- `--goal <TEXT>`: エージェントに達成させる目的・検証タスク
- `--model <NAME>`: 使用するモデル（既定: `gemini-2.5-flash` または `gemini-2.5-pro`）
- `--max-steps <N>`: 最大実行ステップ数（既定: 20）
- `--dry-run`: 操作を行わず、ウィンドウ捕捉とキャプチャのみ確認
- `--report <PATH>`: 結果マトリクス出力先（既定: `work/UI_OPERATION_MATRIX.md`）

### 3. 15FPS 連射疑似動画視覚 (NEW)
ゲーム、UIアニメーション、ホバー・ドラッグの追従確認用の高精度15FPSバースト視覚機能（ゼロディスクI/O RAM保持）。

```powershell
# ① -uicheck: マウスカーソル追従 256x256 クロップ連射 (デフォルト1.5秒 = 約23フレーム)
python .agents/skills/gamerseye/run.py --title "TuskFolio" -uicheck --duration 1.5 --fps 15 [--save-anim uicheck.webp]

# ② -fullview: ウィンドウ全体 15FPS 連射 (ゲーム・動画確認用)
python .agents/skills/gamerseye/run.py --title "TuskFolio" -fullview --duration 1.5 --fps 15 [--save-anim fullview.webp]
```

- `-uicheck`: マウスカーソル中心の 256×256 領域を追従連射
- `-fullview`: ウィンドウ全体を 15FPS 連射
- `--fps <N>`: 連射FPS（既定: 15）
- `--duration <SEC>`: 連射秒数（既定: 1.5秒）
- `--save-anim <PATH>`: 任意で Animated WebP / GIF として保存（省略時はRAMバッファのみ）

