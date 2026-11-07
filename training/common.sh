# Shared settings for the training scripts (they include this file).
set -e
cd "$(dirname "$0")"

# The model you train: small enough for a 16 GB Mac. Options: Qwen/Qwen3-0.6B (fastest),
# Qwen/Qwen3-1.7B (default), Qwen/Qwen3-4B (better, needs ~14 GB free RAM while training).
BASE_MODEL="${BASE_MODEL:-Qwen/Qwen3-1.7B}"
NAME="${NAME:-lumelle-mini}"          # the name of your model in Ollama and in Lumelle
VENV=.venv-train

need_mac() {
  if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
    echo "Training uses MLX, which runs only on a Mac with Apple Silicon (M1–M4)."; exit 1
  fi
  if [ "$(sw_vers -productVersion | cut -d. -f1)" -lt 14 ]; then
    echo "MLX needs macOS 14 Sonoma or newer. Update macOS in System Settings → General → Software Update."; exit 1
  fi
}

pick_python() {
  # MLX needs Python 3.11+. macOS comes with 3.9, so look for a newer one.
  PY=""
  for p in python3.13 python3.12 python3.11 /Library/Frameworks/Python.framework/Versions/3.1[1-3]/bin/python3 /opt/homebrew/bin/python3; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then PY="$p"; break; fi
  done
  if [ -z "$PY" ]; then
    echo "Training needs Python 3.11 or newer (this Mac has $(python3 --version 2>&1))."
    echo "Install it once: https://www.python.org/downloads/macos/ → the latest Python 3.12 or 3.13 → macOS installer,"
    echo "double-click it, then run this script again. Lumelle itself keeps using its own Python."
    exit 1
  fi
  echo "Python for training: $($PY --version)"
}

activate() {
  if [ ! -x "$VENV/bin/python" ]; then
    pick_python
    "$PY" -m venv "$VENV"
  fi
  # shellcheck disable=SC1091
  . "$VENV/bin/activate"
}
