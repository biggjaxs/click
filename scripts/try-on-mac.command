#!/usr/bin/env bash
# =============================================================================
#  Try Click on a Mac WITHOUT a Raspberry Pi.
#  Runs the "box" part (chat app + memory) on this Mac too, at http://127.0.0.1:8080
#
#  First run the Companion installer once:
#      bash ~/Downloads/click/pc-companion/scripts/install-mac.command
#  Then run this (and again any time you want to start Click):
#      bash ~/Downloads/click/scripts/try-on-mac.command
#  Stop it with Ctrl+C in this Terminal window.
#
#  Trial mode differences: memory is NOT encrypted, there's no login (it only listens on this
#  Mac, so nobody else on your network can reach it), and data lives in ~/ClickTrial.
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HUB="$ROOT/pi-hub"
VENV="$HUB/.venv"
PY="$VENV/bin/python"
DATA="$HOME/ClickTrial"
PORT=8080
step() { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }
ok() { printf '\033[32m%s\033[0m\n' "${1:-OK}"; }

export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null || [[ ! -d "$ROOT/pc-companion/.venv" ]]; then
  echo "Please run the Companion installer first:"
  echo "    bash \"$ROOT/pc-companion/scripts/install-mac.command\""
  exit 1
fi

step "Making sure Ollama is running"
open -ga Ollama || true
OLLAMA="$(command -v ollama || echo /Applications/Ollama.app/Contents/Resources/ollama)"
for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break; sleep 1; done
ok

step "Setting up the chat app (first time: 2-5 minutes)"
uv venv --python 3.12 "$VENV" --allow-existing >/dev/null
# Everything except SQLCipher (encryption), which needs extra system libraries on a Mac.
grep -v '^sqlcipher3' "$HUB/requirements.txt" > /tmp/click-trial-reqs.txt
uv pip install --quiet --python "$PY" -r /tmp/click-trial-reqs.txt
ok

step "Downloading the small backup and memory models (first time only)"
"$OLLAMA" pull gemma3:1b
"$OLLAMA" pull all-minilm
ok

step "Pointing the Click Companion at this Mac"
SETTINGS="$HOME/Library/Application Support/ClickCompanion/settings.json"
mkdir -p "$(dirname "$SETTINGS")"
"$PY" - "$SETTINGS" "127.0.0.1:$PORT" <<'PY'
import json, sys
from pathlib import Path
p, host = Path(sys.argv[1]), sys.argv[2]
data = json.loads(p.read_text()) if p.exists() else {}
data["pi_host"] = host
p.write_text(json.dumps(data, indent=2))
PY
launchctl kickstart -k "gui/$(id -u)/com.click.companion" 2>/dev/null || true
ok

step "Starting Click at http://127.0.0.1:$PORT  (press Ctrl+C here to stop)"
mkdir -p "$DATA"
(sleep 4 && open "http://127.0.0.1:$PORT") &
cd "$HUB"
CLICK_DATA_DIR="$DATA" CLICK_ALLOW_UNENCRYPTED=1 CLICK_PC_HOST=127.0.0.1 \
  exec "$VENV/bin/uvicorn" app.main:app --host 127.0.0.1 --port "$PORT"
