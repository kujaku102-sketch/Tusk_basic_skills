#!/usr/bin/env python3
"""
Tusk Batch Runner (`/tuskbatch`) — 3-Way Hybrid Code Refactoring & Batch Task Engine:
  1. `gemini-batch` : Gemini Developer API Batch Queue (`client.batches.create` — 50% cost discount)
  2. `vertex`       : GCP $300 Credit Consumer (`genai.Client(vertexai=True, project=..., location=...)`
                      via concurrent Structured Output or Vertex Batch when `--gcs-uri` is given)
  3. `local-llm`    : GCE / Local LLM Server (`Ollama` / `vLLM` OpenAI-compatible JSON Schema endpoint,
                      e.g. `gemma4:26b`, `gemma-4-31b-it`, `qwen2.5-coder:32b`)
  + GCE SSH Remote Orchestration (`remote-setup`, `remote-run`, `remote-status`, `remote-pull`)

Commands:
  build         --spec <tasks.json> [--work-dir <dir>]
  submit        --work-dir <dir> [--backend gemini-batch|vertex|local-llm] [--model ...] [--wait]
  status        --work-dir <dir> [--wait]
  apply         --work-dir <dir> [--verify] [--verify-cmd "cargo test"]
  run           --spec <tasks.json> [--backend gemini-batch|vertex|local-llm] [--model ...]
  remote-setup  --host <gce-instance-or-ssh-host> [--zone <zone>] [--shutdown-mins 180] [--llm-model gemma4:26b]
  remote-run    --spec <tasks.json> --host <gce-instance-or-ssh-host> [--zone <zone>] [--backend vertex|local-llm|gemini-batch]
  remote-status --host <gce-instance-or-ssh-host> [--zone <zone>] [--remote-dir ~/.tuskbatch/job]
  remote-pull   --work-dir <dir> --host <gce-instance-or-ssh-host> [--zone <zone>] [--verify]
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DEFAULT_MODEL = "gemini-4-argon"
DEFAULT_LOCAL_MODEL = "gemma4:26b"
DEFAULT_VERTEX_LOCATION = "us-central1"

COMPLETED_STATES = {
    "JOB_STATE_SUCCEEDED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
    "JOB_STATE_PAUSED",
}

PATCH_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "task_id": {"type": "STRING"},
        "summary": {"type": "STRING"},
        "edits": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "file": {
                        "type": "STRING",
                        "description": "Relative file path from project_root.",
                    },
                    "target": {
                        "type": "STRING",
                        "description": "Exact verbatim substring from the provided source slice to replace.",
                    },
                    "replacement": {
                        "type": "STRING",
                        "description": "Complete drop-in code replacement for target.",
                    },
                    "rationale": {
                        "type": "STRING",
                        "description": "Concise technical reason for this edit.",
                    },
                },
                "required": ["file", "target", "replacement", "rationale"],
            },
        },
    },
    "required": ["task_id", "summary", "edits"],
}

DEFAULT_SYSTEM_INSTRUCTION = """You are a senior software architect performing surgical code refactoring.
Rules:
1. Return ONLY valid JSON matching the requested `responseSchema`.
2. Every `target` field in `edits` MUST be an exact, character-for-character substring of the provided source code slice (preserving exact indentation and line breaks).
3. Do NOT break existing public signatures, struct field initializers, or unit tests.
4. Preserve existing comments and documentation integrity."""


def load_env_vars(project_root: Path | None) -> None:
    if project_root:
        env_file = project_root / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_env_key(project_root: Path | None, explicit_api_key: str | None = None) -> str:
    load_env_vars(project_root)
    api_key = (
        explicit_api_key
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or ""
    ).strip()

    if not api_key or api_key == "..." or len(api_key) < 15:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as reg_key:
                reg_val, _ = winreg.QueryValueEx(reg_key, "GEMINI_API_KEY")
                if reg_val and reg_val != "..." and len(str(reg_val)) >= 15:
                    api_key = str(reg_val).strip()
        except Exception:
            pass

    if not api_key or api_key == "..." or len(api_key) < 15:
        raise SystemExit(
            "[ERROR] Valid GEMINI_API_KEY is not configured.\n"
            "Pass `--api-key <KEY>`, set `$env:GEMINI_API_KEY`, or use `--backend vertex` with GCP ADC."
        )
    return api_key


def resolve_gcp_project(explicit_project: str | None = None) -> str:
    proj = (
        explicit_project
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCLOUD_PROJECT")
        or ""
    ).strip()
    if proj:
        return proj
    try:
        proc = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            capture_output=True,
            text=True,
            shell=(os.name == "nt"),
            timeout=10,
        )
        val = proc.stdout.strip()
        if proc.returncode == 0 and val and val != "(unset)":
            return val
    except Exception:
        pass
    raise SystemExit(
        "[ERROR] GCP Project ID could not be resolved for `--backend vertex`.\n"
        "Pass `--project <GCP_PROJECT_ID>` or set `GOOGLE_CLOUD_PROJECT`."
    )


def get_client(
    project_root: Path | None = None,
    explicit_api_key: str | None = None,
    vertex: bool = False,
    project: str | None = None,
    location: str | None = None,
):
    try:
        from google import genai
    except ImportError as exc:
        raise SystemExit(
            "[ERROR] `google-genai` SDK is required. Install with: pip install -U google-genai"
        ) from exc

    load_env_vars(project_root)
    if vertex:
        gcp_proj = resolve_gcp_project(project)
        gcp_loc = location or os.environ.get("GOOGLE_CLOUD_LOCATION") or DEFAULT_VERTEX_LOCATION
        print(f"[VERTEX] Initializing Vertex AI client (project={gcp_proj}, location={gcp_loc}) — consumes GCP credits")
        return genai.Client(vertexai=True, project=gcp_proj, location=gcp_loc)

    api_key = load_env_key(project_root, explicit_api_key)
    return genai.Client(api_key=api_key)


def read_slice(file_path: Path, start_line: int, end_line: int) -> str:
    lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
    return "".join(lines[max(0, start_line - 1) : end_line])


def build_from_spec(spec_path: Path, work_dir: Path) -> tuple[Path, dict]:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    project_root = Path(spec.get("project_root", spec_path.parent)).resolve()
    sys_inst = spec.get("system_instruction", DEFAULT_SYSTEM_INSTRUCTION)
    temperature = float(spec.get("temperature", 0.1))

    work_dir.mkdir(parents=True, exist_ok=True)
    requests_jsonl = work_dir / "batch_requests.jsonl"

    rows = []
    for task in spec.get("tasks", []):
        task_id = task["id"]
        title = task.get("title", task_id)
        requirement = task["requirement"]

        slice_blocks = []
        for s in task.get("slices", []):
            rel_file = s["file"].replace("\\", "/")
            abs_file = (project_root / rel_file).resolve()
            if not abs_file.exists():
                raise SystemExit(f"[ERROR] Slice file not found: {abs_file}")
            start_l = int(s.get("start_line", 1))
            end_l = int(s.get("end_line", 999999))
            code = read_slice(abs_file, start_l, end_l)
            lang = s.get("lang", abs_file.suffix.lstrip(".") or "text")
            slice_blocks.append(
                f"### Target File: `{rel_file}` (lines {start_l}-{end_l})\n```{lang}\n{code}\n```"
            )

        slices_text = "\n\n".join(slice_blocks)
        prompt = f"## Task `{task_id}`: {title}\n\n### Requirement\n{requirement}\n\n{slices_text}\n"

        row = {
            "key": task_id,
            "request": {
                "system_instruction": {"parts": [{"text": sys_inst}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generation_config": {
                    "temperature": temperature,
                    "response_mime_type": "application/json",
                    "response_schema": PATCH_RESPONSE_SCHEMA,
                },
            },
        }
        rows.append(row)

    with requests_jsonl.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    meta_path = work_dir / "batch_spec_meta.json"
    meta = {
        "spec_path": str(spec_path.resolve()),
        "project_root": str(project_root),
        "backend": spec.get("backend", "gemini-batch"),
        "model": spec.get("model", DEFAULT_MODEL),
        "project": spec.get("project"),
        "location": spec.get("location", DEFAULT_VERTEX_LOCATION),
        "base_url": spec.get("base_url", "http://localhost:11434/v1"),
        "verify_cmd": spec.get("verify_cmd"),
        "requests_jsonl": str(requests_jsonl),
        "task_keys": [r["key"] for r in rows],
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Built {len(rows)} batch requests -> {requests_jsonl}")
    return requests_jsonl, meta


def cmd_build(args: argparse.Namespace) -> None:
    spec_path = Path(args.spec).resolve()
    work_dir = Path(args.work_dir).resolve() if args.work_dir else spec_path.parent
    build_from_spec(spec_path, work_dir)


def run_vertex_direct_batch(
    client,
    model: str,
    rows: list[dict],
    results_jsonl: Path,
    max_workers: int = 4,
) -> None:
    """Executes all JSONL rows concurrently against Vertex AI (consuming GCP $300 credit without GCS buckets)."""
    from google.genai import types

    def _call_one(row: dict) -> dict:
        key = row["key"]
        req = row["request"]
        prompt_text = req["contents"][0]["parts"][0]["text"]
        sys_inst = req.get("system_instruction", {}).get("parts", [{}])[0].get("text", DEFAULT_SYSTEM_INSTRUCTION)
        gen_cfg = req.get("generation_config", {})
        try:
            resp = client.models.generate_content(
                model=model,
                contents=prompt_text,
                config=types.GenerateContentConfig(
                    system_instruction=sys_inst,
                    temperature=gen_cfg.get("temperature", 0.1),
                    response_mime_type="application/json",
                    response_schema=gen_cfg.get("response_schema", PATCH_RESPONSE_SCHEMA),
                ),
            )
            return {"key": key, "text": resp.text, "error": None}
        except Exception as exc:
            return {"key": key, "text": None, "error": str(exc)}

    print(f"[VERTEX] Running {len(rows)} task(s) on Vertex AI ({model}, workers={max_workers})...")
    ordered_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_call_one, r) for r in rows]
        for fut in futures:
            res = fut.result()
            if res.get("error"):
                print(f"  [ERR] {res['key']}: {res['error']}", file=sys.stderr)
            else:
                print(f"  [OK] Completed {res['key']}")
            ordered_results.append(res)

    with results_jsonl.open("w", encoding="utf-8") as f:
        for r in ordered_results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[OK] Saved Vertex AI results -> {results_jsonl}")


def run_local_llm_batch(
    base_url: str,
    model: str,
    rows: list[dict],
    results_jsonl: Path,
    max_workers: int = 2,
) -> None:
    """Executes all JSONL rows against an OpenAI-compatible local LLM server (Ollama / vLLM on GCE)."""
    endpoint = base_url.rstrip("/") + "/chat/completions"
    schema_hint = json.dumps(PATCH_RESPONSE_SCHEMA, indent=2)

    def _call_local(row: dict) -> dict:
        key = row["key"]
        req = row["request"]
        prompt_text = req["contents"][0]["parts"][0]["text"]
        sys_inst = req.get("system_instruction", {}).get("parts", [{}])[0].get("text", DEFAULT_SYSTEM_INSTRUCTION)
        full_sys = (
            f"{sys_inst}\n\nYou MUST output a single valid JSON object conforming to this schema:\n{schema_hint}"
        )
        payload = {
            "model": model,
            "temperature": req.get("generation_config", {}).get("temperature", 0.1),
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": full_sys},
                {"role": "user", "content": prompt_text},
            ],
        }
        data = json.dumps(payload).encode("utf-8")
        http_req = urllib.request.Request(
            endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_req, timeout=600) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                content = body["choices"][0]["message"]["content"]
                return {"key": key, "text": content, "error": None}
        except Exception as exc:
            return {"key": key, "text": None, "error": str(exc)}

    print(f"[LOCAL-LLM] Sending {len(rows)} task(s) to {endpoint} (model={model})...")
    ordered_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_call_local, r) for r in rows]
        for fut in futures:
            res = fut.result()
            if res.get("error"):
                print(f"  [ERR] {res['key']}: {res['error']}", file=sys.stderr)
            else:
                print(f"  [OK] Completed {res['key']}")
            ordered_results.append(res)

    with results_jsonl.open("w", encoding="utf-8") as f:
        for r in ordered_results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[OK] Saved Local LLM results -> {results_jsonl}")


def submit_job(
    work_dir: Path,
    backend_override: str | None = None,
    model_override: str | None = None,
    inline: bool = False,
    wait: bool = False,
    poll_interval: int = 20,
    api_key: str | None = None,
    project: str | None = None,
    location: str | None = None,
    base_url: str | None = None,
) -> dict:
    from google.genai import types

    meta_path = work_dir / "batch_spec_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    project_root = Path(meta["project_root"]) if "project_root" in meta else work_dir
    requests_jsonl = Path(meta.get("requests_jsonl", work_dir / "batch_requests.jsonl"))
    if not requests_jsonl.exists():
        alt = work_dir / "table_bbox_refactor_requests.jsonl"
        if alt.exists():
            requests_jsonl = alt
        else:
            raise SystemExit(f"[ERROR] Requests JSONL not found in {work_dir}")

    backend = backend_override or meta.get("backend") or "gemini-batch"
    rows = [
        json.loads(line)
        for line in requests_jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    results_jsonl = work_dir / "batch_results.jsonl"
    state_path = work_dir / "batch_job_state.json"

    if backend == "vertex":
        model = model_override or meta.get("model") or DEFAULT_MODEL
        client = get_client(
            project_root=project_root,
            vertex=True,
            project=project or meta.get("project"),
            location=location or meta.get("location"),
        )
        run_vertex_direct_batch(client, model, rows, results_jsonl)
        state_data = {
            "job_name": f"vertex-direct/{work_dir.name}",
            "backend": "vertex",
            "model": model,
            "state": "JOB_STATE_SUCCEEDED",
            "project_root": str(project_root),
            "verify_cmd": meta.get("verify_cmd"),
            "keys": [r["key"] for r in rows],
        }
        state_path.write_text(json.dumps(state_data, indent=2, ensure_ascii=False), encoding="utf-8")
        return state_data

    if backend == "local-llm":
        model = model_override or meta.get("model") or DEFAULT_LOCAL_MODEL
        if model == DEFAULT_MODEL:
            model = DEFAULT_LOCAL_MODEL
        llm_url = base_url or meta.get("base_url") or "http://localhost:11434/v1"
        run_local_llm_batch(llm_url, model, rows, results_jsonl)
        state_data = {
            "job_name": f"local-llm/{model}",
            "backend": "local-llm",
            "model": model,
            "base_url": llm_url,
            "state": "JOB_STATE_SUCCEEDED",
            "project_root": str(project_root),
            "verify_cmd": meta.get("verify_cmd"),
            "keys": [r["key"] for r in rows],
        }
        state_path.write_text(json.dumps(state_data, indent=2, ensure_ascii=False), encoding="utf-8")
        return state_data

    # Default: `gemini-batch` (Gemini Developer API 50% discount Batch Queue)
    model = model_override or meta.get("model") or DEFAULT_MODEL
    client = get_client(project_root, api_key)

    if inline:
        inline_requests = []
        for r in rows:
            req = r["request"]
            cfg = dict(req.get("generation_config", {}))
            if "system_instruction" in req:
                cfg["system_instruction"] = req["system_instruction"]
            inline_requests.append({"contents": req["contents"], "config": cfg})
        batch_job = client.batches.create(
            model=model,
            src=inline_requests,
            config={"display_name": f"tuskbatch-{work_dir.name}-inline"},
        )
        mode = "inline"
        uploaded_name = None
    else:
        uploaded = client.files.upload(
            file=str(requests_jsonl),
            config=types.UploadFileConfig(
                display_name=f"tuskbatch-{work_dir.name}",
                mime_type="jsonl",
            ),
        )
        print(f"[OK] Uploaded JSONL file: {uploaded.name}")
        batch_job = client.batches.create(
            model=model,
            src=uploaded.name,
            config={"display_name": f"tuskbatch-{work_dir.name}"},
        )
        mode = "file"
        uploaded_name = uploaded.name

    state_name = getattr(batch_job.state, "name", str(batch_job.state))
    state_data = {
        "job_name": batch_job.name,
        "backend": "gemini-batch",
        "model": model,
        "mode": mode,
        "uploaded_file": uploaded_name,
        "state": state_name,
        "project_root": str(project_root),
        "verify_cmd": meta.get("verify_cmd"),
        "keys": [r["key"] for r in rows],
    }
    state_path.write_text(json.dumps(state_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Created Batch Job: {batch_job.name} (model={model}, state={state_name})")

    if wait:
        poll_and_download(client, work_dir, state_data, poll_interval=poll_interval)
    return state_data


def poll_and_download(client, work_dir: Path, state_data: dict, poll_interval: int = 20) -> Path:
    results_jsonl = work_dir / "batch_results.jsonl"
    if state_data.get("backend") in ("vertex", "local-llm") and results_jsonl.exists():
        return results_jsonl

    job_name = state_data["job_name"]
    state_path = work_dir / "batch_job_state.json"

    batch_job = client.batches.get(name=job_name)
    state_name = getattr(batch_job.state, "name", str(batch_job.state))

    while state_name not in COMPLETED_STATES:
        print(f"[WAIT] {job_name} -> {state_name} (sleeping {poll_interval}s)")
        time.sleep(poll_interval)
        batch_job = client.batches.get(name=job_name)
        state_name = getattr(batch_job.state, "name", str(batch_job.state))

    state_data["state"] = state_name
    state_path.write_text(json.dumps(state_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[DONE] Job {job_name} finished with state: {state_name}")

    if state_name != "JOB_STATE_SUCCEEDED":
        if getattr(batch_job, "error", None):
            print(f"[ERROR] {batch_job.error}", file=sys.stderr)
        raise SystemExit(1)

    if batch_job.dest and getattr(batch_job.dest, "file_name", None):
        result_file = batch_job.dest.file_name
        print(f"[OK] Downloading result file: {result_file}")
        raw_bytes = client.files.download(file=result_file)
        results_jsonl.write_bytes(raw_bytes)
        print(f"[OK] Saved batch results -> {results_jsonl}")
    elif batch_job.dest and getattr(batch_job.dest, "inlined_responses", None):
        keys = state_data.get("keys", [])
        with results_jsonl.open("w", encoding="utf-8") as f:
            for idx, resp in enumerate(batch_job.dest.inlined_responses):
                key = keys[idx] if idx < len(keys) else f"task_{idx + 1}"
                text = getattr(resp.response, "text", None) if getattr(resp, "response", None) else None
                row = {
                    "key": key,
                    "text": text,
                    "error": str(resp.error) if getattr(resp, "error", None) else None,
                }
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"[OK] Saved inline batch results -> {results_jsonl}")
    return results_jsonl


def cmd_submit(args: argparse.Namespace) -> None:
    work_dir = Path(args.work_dir).resolve()
    submit_job(
        work_dir=work_dir,
        backend_override=args.backend,
        model_override=args.model,
        inline=args.inline,
        wait=args.wait,
        poll_interval=args.poll_interval,
        api_key=args.api_key,
        project=args.project,
        location=args.location,
        base_url=args.base_url,
    )


def cmd_status(args: argparse.Namespace) -> None:
    work_dir = Path(args.work_dir).resolve()
    state_path = work_dir / "batch_job_state.json"
    if not state_path.exists():
        raise SystemExit(f"[ERROR] State file not found: {state_path}")
    state_data = json.loads(state_path.read_text(encoding="utf-8"))
    if state_data.get("backend") in ("vertex", "local-llm"):
        print(json.dumps(state_data, indent=2, ensure_ascii=False))
        return

    project_root = Path(state_data.get("project_root", work_dir))
    client = get_client(project_root, args.api_key)

    if args.wait:
        poll_and_download(client, work_dir, state_data, poll_interval=args.poll_interval)
    else:
        batch_job = client.batches.get(name=state_data["job_name"])
        state_name = getattr(batch_job.state, "name", str(batch_job.state))
        state_data["state"] = state_name
        state_path.write_text(json.dumps(state_data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(state_data, indent=2, ensure_ascii=False))
        results_jsonl = work_dir / "batch_results.jsonl"
        if state_name == "JOB_STATE_SUCCEEDED" and not results_jsonl.exists():
            poll_and_download(client, work_dir, state_data, poll_interval=1)


def extract_patch_payload(row: dict) -> dict:
    if "text" in row and row["text"]:
        return json.loads(row["text"])
    resp = row.get("response", {})
    candidates = resp.get("candidates", [])
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        if parts and "text" in parts[0]:
            return json.loads(parts[0]["text"])
    raise ValueError(f"Could not extract JSON payload from batch row: {row.get('key')}")


def apply_results(
    work_dir: Path,
    results_override: str | None = None,
    project_root_override: str | None = None,
    verify: bool = True,
    verify_cmd_override: str | None = None,
) -> None:
    state_path = work_dir / "batch_job_state.json"
    state_data = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}

    project_root = Path(
        project_root_override or state_data.get("project_root") or work_dir
    ).resolve()
    verify_cmd = verify_cmd_override or state_data.get("verify_cmd")

    results_path = Path(results_override) if results_override else (work_dir / "batch_results.jsonl")
    if not results_path.exists():
        alt = work_dir / "table_bbox_refactor_results.jsonl"
        if alt.exists():
            results_path = alt
        else:
            raise SystemExit(f"[ERROR] Results JSONL not found: {results_path}")

    rows = [
        json.loads(line)
        for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    backups: dict[Path, str] = {}
    applied_count = 0

    for row in rows:
        payload = extract_patch_payload(row)
        task_id = payload.get("task_id", row.get("key", "unknown"))
        print(f"[TASK] {task_id}: {payload.get('summary', '')}")
        for edit in payload.get("edits", []):
            rel_file = edit["file"].replace("\\", "/")
            file_path = (project_root / rel_file).resolve()
            if not file_path.exists():
                raise SystemExit(f"[ERROR] Target file does not exist: {file_path}")

            if file_path not in backups:
                backups[file_path] = file_path.read_text(encoding="utf-8")

            current_text = file_path.read_text(encoding="utf-8")
            use_crlf = "\r\n" in current_text
            norm_current = current_text.replace("\r\n", "\n")
            norm_target = edit["target"].replace("\r\n", "\n")
            norm_replacement = edit["replacement"].replace("\r\n", "\n")

            if (
                "crate::udr::Annotation {" in norm_replacement
                and "severity_label:" not in norm_replacement
            ):
                norm_replacement = norm_replacement.replace(
                    "severity: crate::udr::AnnotationSeverity::Info,\n",
                    "severity: crate::udr::AnnotationSeverity::Info,\n                            severity_label: None,\n",
                )

            if norm_target not in norm_current:
                print(f"  [WARN] Target block not found verbatim in {rel_file} for {task_id}")
                continue

            updated = norm_current.replace(norm_target, norm_replacement, 1)
            if use_crlf:
                updated = updated.replace("\n", "\r\n")
            file_path.write_text(updated, encoding="utf-8")
            applied_count += 1
            print(f"  [APPLIED] {rel_file} ({edit.get('rationale', '')})")

    print(f"[OK] Applied {applied_count} edit blocks across {len(backups)} file(s).")

    if verify and verify_cmd and applied_count > 0:
        print(f"[VERIFY] Running `{verify_cmd}` in {project_root}...")
        proc = subprocess.run(
            verify_cmd,
            cwd=str(project_root),
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            print(f"[FAIL] `{verify_cmd}` failed! Rolling back modified files...", file=sys.stderr)
            print(proc.stderr[-2000:], file=sys.stderr)
            for path, orig_content in backups.items():
                path.write_text(orig_content, encoding="utf-8")
            raise SystemExit(proc.returncode)
        print(f"[PASS] `{verify_cmd}` succeeded with all batch patches applied!")


def cmd_apply(args: argparse.Namespace) -> None:
    work_dir = Path(args.work_dir).resolve()
    apply_results(
        work_dir=work_dir,
        results_override=args.results,
        project_root_override=args.project_root,
        verify=args.verify,
        verify_cmd_override=args.verify_cmd,
    )


def cmd_run(args: argparse.Namespace) -> None:
    spec_path = Path(args.spec).resolve()
    work_dir = Path(args.work_dir).resolve() if args.work_dir else spec_path.parent
    build_from_spec(spec_path, work_dir)
    submit_job(
        work_dir=work_dir,
        backend_override=args.backend,
        model_override=args.model,
        inline=args.inline,
        wait=True,
        poll_interval=args.poll_interval,
        api_key=args.api_key,
        project=args.project,
        location=args.location,
        base_url=args.base_url,
    )
    apply_results(
        work_dir=work_dir,
        verify=True,
        verify_cmd_override=args.verify_cmd,
    )


# ==============================================================================
# Remote GCE Orchestration (`remote-setup`, `remote-run`, `remote-status`, `remote-pull`)
# Supports both standard `ssh`/`scp` and `gcloud compute ssh`/`gcloud compute scp`
# ==============================================================================


def _run_ssh(host: str, zone: str | None, remote_cmd: str) -> None:
    if zone:
        cmd = ["gcloud", "compute", "ssh", host, f"--zone={zone}", "--command", remote_cmd]
    else:
        cmd = ["ssh", host, remote_cmd]
    res = subprocess.run(cmd, shell=(os.name == "nt"))
    if res.returncode != 0:
        raise SystemExit(res.returncode)


def _run_scp_to(host: str, zone: str | None, local_files: list[str], remote_dest: str) -> None:
    if zone:
        cmd = ["gcloud", "compute", "scp", *local_files, f"{host}:{remote_dest}", f"--zone={zone}"]
    else:
        cmd = ["scp", *local_files, f"{host}:{remote_dest}"]
    res = subprocess.run(cmd, shell=(os.name == "nt"))
    if res.returncode != 0:
        raise SystemExit(res.returncode)


def _run_scp_from(host: str, zone: str | None, remote_src: str, local_dest: str) -> None:
    if zone:
        cmd = ["gcloud", "compute", "scp", f"{host}:{remote_src}", local_dest, f"--zone={zone}"]
    else:
        cmd = ["scp", f"{host}:{remote_src}", local_dest]
    res = subprocess.run(cmd, shell=(os.name == "nt"))
    if res.returncode != 0:
        raise SystemExit(res.returncode)


def cmd_remote_setup(args: argparse.Namespace) -> None:
    setup_sh = Path(__file__).resolve().parent / "gce_forge_setup.sh"
    if not setup_sh.exists():
        raise SystemExit(f"[ERROR] Setup script not found: {setup_sh}")

    _run_ssh(args.host, args.zone, "mkdir -p ~/.tuskbatch")
    _run_scp_to(args.host, args.zone, [str(setup_sh), str(Path(__file__).resolve())], "~/.tuskbatch/")
    flags = f"--shutdown-mins {args.shutdown_mins} --model {args.llm_model}"
    if args.no_rust:
        flags += " --no-rust"
    if args.no_ollama:
        flags += " --no-ollama"
    _run_ssh(args.host, args.zone, f"bash ~/.tuskbatch/gce_forge_setup.sh {flags}")


def cmd_remote_run(args: argparse.Namespace) -> None:
    spec_path = Path(args.spec).resolve()
    work_dir = Path(args.work_dir).resolve() if args.work_dir else spec_path.parent
    requests_jsonl, meta = build_from_spec(spec_path, work_dir)
    meta_path = work_dir / "batch_spec_meta.json"

    remote_dir = args.remote_dir
    _run_ssh(args.host, args.zone, f"mkdir -p {remote_dir}")
    _run_scp_to(
        args.host,
        args.zone,
        [str(Path(__file__).resolve()), str(requests_jsonl), str(meta_path)],
        f"{remote_dir}/",
    )

    backend = args.backend or meta.get("backend") or "vertex"
    model_arg = f"--model {args.model}" if args.model else ""
    proj_arg = f"--project {args.project}" if args.project else ""
    remote_cmd = (
        f"nohup ~/.tuskbatch/venv/bin/python {remote_dir}/tusk_batch_runner.py "
        f"submit --work-dir {remote_dir} --backend {backend} {model_arg} {proj_arg} --wait "
        f"> {remote_dir}/remote_batch.log 2>&1 &"
    )
    _run_ssh(args.host, args.zone, remote_cmd)
    print(f"[OK] Launched detached remote job on {args.host}:{remote_dir} (backend={backend}).")
    print("You can now safely power off your local PC!")


def cmd_remote_status(args: argparse.Namespace) -> None:
    remote_dir = args.remote_dir
    _run_ssh(
        args.host,
        args.zone,
        f"cat {remote_dir}/batch_job_state.json 2>/dev/null || true; echo '--- LOG ---'; tail -n 25 {remote_dir}/remote_batch.log 2>/dev/null || true",
    )


def cmd_remote_pull(args: argparse.Namespace) -> None:
    work_dir = Path(args.work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    remote_dir = args.remote_dir
    _run_scp_from(args.host, args.zone, f"{remote_dir}/batch_results.jsonl", str(work_dir / "batch_results.jsonl"))
    _run_scp_from(args.host, args.zone, f"{remote_dir}/batch_job_state.json", str(work_dir / "batch_job_state.json"))
    print(f"[OK] Pulled batch_results.jsonl from {args.host}:{remote_dir} -> {work_dir}")
    if args.apply:
        apply_results(work_dir=work_dir, verify=args.verify, verify_cmd_override=args.verify_cmd)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tusk Batch API & GCE Hybrid Runner (/tuskbatch)")
    parser.add_argument("--api-key", default=None, help="Explicit Gemini API key")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="Build requests JSONL from tasks.json spec")
    p_build.add_argument("--spec", required=True, help="Path to tasks.json specification")
    p_build.add_argument("--work-dir", default=None, help="Output directory (defaults to spec parent dir)")
    p_build.set_defaults(func=cmd_build)

    p_sub = sub.add_parser("submit", help="Submit batch job (gemini-batch | vertex | local-llm)")
    p_sub.add_argument("--work-dir", default=".", help="Working directory containing batch_requests.jsonl")
    p_sub.add_argument(
        "--backend",
        choices=["gemini-batch", "vertex", "local-llm"],
        default=None,
        help="Execution backend (default: gemini-batch, or vertex for $300 GCP credit, or local-llm for Ollama/vLLM)",
    )
    p_sub.add_argument("--model", default=None, help="Model override (e.g. gemini-4-argon, gemma4:26b)")
    p_sub.add_argument("--project", default=None, help="GCP Project ID (for --backend vertex)")
    p_sub.add_argument("--location", default=None, help="GCP Location (default: us-central1)")
    p_sub.add_argument("--base-url", default=None, help="Local LLM OpenAI-compatible URL (default: http://localhost:11434/v1)")
    p_sub.add_argument("--inline", action="store_true", help="Submit inline instead of File API upload")
    p_sub.add_argument("--wait", action="store_true", help="Wait for completion and download results")
    p_sub.add_argument("--poll-interval", type=int, default=20, help="Polling interval in seconds")
    p_sub.set_defaults(func=cmd_submit)

    p_stat = sub.add_parser("status", help="Check status or wait for active Batch API job")
    p_stat.add_argument("--work-dir", default=".", help="Working directory containing batch_job_state.json")
    p_stat.add_argument("--wait", action="store_true", help="Wait for completion and download results")
    p_stat.add_argument("--poll-interval", type=int, default=20, help="Polling interval in seconds")
    p_stat.set_defaults(func=cmd_status)

    p_app = sub.add_parser("apply", help="Apply downloaded batch results and run verification command")
    p_app.add_argument("--work-dir", default=".", help="Working directory containing batch_results.jsonl")
    p_app.add_argument("--results", default=None, help="Explicit path to results JSONL")
    p_app.add_argument("--project-root", default=None, help="Explicit project root directory")
    p_app.add_argument("--verify", action="store_true", default=True, help="Run verify_cmd and rollback on failure")
    p_app.add_argument("--verify-cmd", default=None, help="Override verification command (e.g. 'cargo test')")
    p_app.set_defaults(func=cmd_apply)

    p_run = sub.add_parser("run", help="One-shot: build -> submit --wait -> apply --verify")
    p_run.add_argument("--spec", required=True, help="Path to tasks.json specification")
    p_run.add_argument("--work-dir", default=None, help="Working directory")
    p_run.add_argument("--backend", choices=["gemini-batch", "vertex", "local-llm"], default=None)
    p_run.add_argument("--model", default=None, help="Model override")
    p_run.add_argument("--project", default=None, help="GCP Project ID (for --backend vertex)")
    p_run.add_argument("--location", default=None, help="GCP Location (for --backend vertex)")
    p_run.add_argument("--base-url", default=None, help="Local LLM OpenAI-compatible URL")
    p_run.add_argument("--inline", action="store_true", help="Submit inline requests")
    p_run.add_argument("--poll-interval", type=int, default=20, help="Polling interval in seconds")
    p_run.add_argument("--verify-cmd", default=None, help="Override verification command")
    p_run.set_defaults(func=cmd_run)

    p_rsetup = sub.add_parser("remote-setup", help="Provision GCE instance with Ollama (Gemma 4), Vertex SDK, Rust & auto-shutdown")
    p_rsetup.add_argument("--host", required=True, help="SSH host or GCE instance name")
    p_rsetup.add_argument("--zone", default=None, help="GCE zone (if using gcloud compute ssh)")
    p_rsetup.add_argument("--shutdown-mins", type=int, default=180, help="Auto-poweroff timer in minutes (default: 180)")
    p_rsetup.add_argument("--llm-model", default=DEFAULT_LOCAL_MODEL, help="Ollama model tag to pull (default: gemma4:26b)")
    p_rsetup.add_argument("--no-rust", action="store_true", help="Skip Rust installation")
    p_rsetup.add_argument("--no-ollama", action="store_true", help="Skip Ollama installation")
    p_rsetup.set_defaults(func=cmd_remote_setup)

    p_rrun = sub.add_parser("remote-run", help="Build locally, upload to GCE, and launch detached background job")
    p_rrun.add_argument("--spec", required=True, help="Path to local tasks.json specification")
    p_rrun.add_argument("--work-dir", default=None, help="Local working directory")
    p_rrun.add_argument("--host", required=True, help="SSH host or GCE instance name")
    p_rrun.add_argument("--zone", default=None, help="GCE zone (if using gcloud compute ssh)")
    p_rrun.add_argument("--remote-dir", default="~/.tuskbatch/job", help="Remote working directory")
    p_rrun.add_argument("--backend", choices=["gemini-batch", "vertex", "local-llm"], default=None)
    p_rrun.add_argument("--model", default=None)
    p_rrun.add_argument("--project", default=None)
    p_rrun.set_defaults(func=cmd_remote_run)

    p_rstat = sub.add_parser("remote-status", help="Check remote job state and log tail over SSH")
    p_rstat.add_argument("--host", required=True, help="SSH host or GCE instance name")
    p_rstat.add_argument("--zone", default=None, help="GCE zone")
    p_rstat.add_argument("--remote-dir", default="~/.tuskbatch/job", help="Remote working directory")
    p_rstat.set_defaults(func=cmd_remote_status)

    p_rpull = sub.add_parser("remote-pull", help="Pull completed results from GCE and apply + verify locally")
    p_rpull.add_argument("--work-dir", default=".", help="Local working directory")
    p_rpull.add_argument("--host", required=True, help="SSH host or GCE instance name")
    p_rpull.add_argument("--zone", default=None, help="GCE zone")
    p_rpull.add_argument("--remote-dir", default="~/.tuskbatch/job", help="Remote working directory")
    p_rpull.add_argument("--apply", action="store_true", default=True, help="Apply patches after pulling")
    p_rpull.add_argument("--verify", action="store_true", default=True, help="Run verify_cmd after applying")
    p_rpull.add_argument("--verify-cmd", default=None, help="Override verification command")
    p_rpull.set_defaults(func=cmd_remote_pull)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
