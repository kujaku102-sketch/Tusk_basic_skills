#!/usr/bin/env bash
# ==============================================================================
# Tusk Batch GCE Forge Setup Script (`gce_forge_setup.sh`)
# Provisions a Google Compute Engine (GCE) instance as:
#   1. Auto-Shutdown Dead-Man's Switch (protects $300 GCP credit from idle burn)
#   2. Python + `google-genai` (Vertex AI / Gemini 4 Batch runner)
#   3. Local LLM Server via Ollama (`gemma4:26b` MoE / `gemma4:31b` / `qwen2.5-coder:32b`)
#   4. Rust Toolchain (`cargo test` verification worker)
#
# Usage on GCE (or via `tusk_batch_runner.py remote-setup`):
#   bash gce_forge_setup.sh [--shutdown-mins 180] [--model gemma4:26b] [--with-rust]
# ==============================================================================

set -euo pipefail

SHUTDOWN_MINS=180
LLM_MODEL="gemma4:26b"
INSTALL_RUST=1
INSTALL_OLLAMA=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --shutdown-mins)
      SHUTDOWN_MINS="$2"
      shift 2
      ;;
    --model)
      LLM_MODEL="$2"
      shift 2
      ;;
    --no-rust)
      INSTALL_RUST=0
      shift
      ;;
    --no-ollama)
      INSTALL_OLLAMA=0
      shift
      ;;
    *)
      echo "[WARN] Unknown argument: $1"
      shift
      ;;
  esac
done

echo "============================================================"
echo " Tusk Batch GCE Forge Provisioning"
echo " Auto-Shutdown Timer : ${SHUTDOWN_MINS} mins"
echo " Local LLM Model     : ${LLM_MODEL}"
echo " Install Rust        : ${INSTALL_RUST}"
echo "============================================================"

# 1. Dead-Man's Switch: Schedule automatic VM poweroff to protect GCP credits
if [[ "${SHUTDOWN_MINS}" -gt 0 ]]; then
  sudo shutdown -c 2>/dev/null || true
  sudo shutdown -h +"${SHUTDOWN_MINS}" "Tusk Batch credit guard: auto-poweroff after ${SHUTDOWN_MINS} minutes."
  echo "[OK] Scheduled automatic instance shutdown in ${SHUTDOWN_MINS} minutes (cancel with: sudo shutdown -c)."
fi

# 2. System packages & Python environment
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv curl git tmux jq build-essential pkg-config libssl-dev

mkdir -p "$HOME/.tuskbatch"
if [[ ! -d "$HOME/.tuskbatch/venv" ]]; then
  python3 -m venv "$HOME/.tuskbatch/venv"
fi
"$HOME/.tuskbatch/venv/bin/pip" install -U pip "google-genai>=2.30.1" pydantic requests

echo "[OK] Python venv ready at $HOME/.tuskbatch/venv"

# 3. Rust toolchain (optional, for running `cargo test` on GCE)
if [[ "${INSTALL_RUST}" -eq 1 ]]; then
  if ! command -v cargo >/dev/null 2>&1; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
  fi
  source "$HOME/.cargo/env"
  echo "[OK] Rust toolchain: $(cargo --version)"
fi

# 4. Ollama Local LLM Server + Model Pull
if [[ "${INSTALL_OLLAMA}" -eq 1 ]]; then
  if ! command -v ollama >/dev/null 2>&1; then
    curl -fsSL https://ollama.com/install.sh | sh
  fi

  # Ensure Ollama listens on 0.0.0.0:11434 (for SSH tunnel or internal VPC calls)
  sudo mkdir -p /etc/systemd/system/ollama.service.d
  sudo tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null <<'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_NUM_PARALLEL=2"
Environment="OLLAMA_FLASH_ATTENTION=1"
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable ollama
  sudo systemctl restart ollama
  sleep 3

  echo "[INFO] Pulling local LLM model: ${LLM_MODEL} ..."
  ollama pull "${LLM_MODEL}" || echo "[WARN] Could not pull ${LLM_MODEL} automatically; verify model tag with 'ollama list'."
  echo "[OK] Ollama service active on port 11434."
fi

echo "============================================================"
echo " [READY] GCE Forge setup complete!"
echo " - Extend or cancel shutdown timer : sudo shutdown -h +240 / sudo shutdown -c"
echo " - Python runner                   : ~/.tuskbatch/venv/bin/python"
echo "============================================================"
