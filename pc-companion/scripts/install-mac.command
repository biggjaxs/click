#!/usr/bin/env bash
# =============================================================================
#  Click Companion - Mac installer
#  Installs: uv (Python manager), Python 3.12, Ollama, the Companion app, the AI models
#  that fit this Mac, and auto-start. Safe to run again.
#  Run it from Terminal:   bash ~/Downloads/click/pc-companion/scripts/install-mac.command
# =============================================================================
set -euo pipefail
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$SCRIPTS")"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"
step() { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }
ok() { printf '\033[32m%s\033[0m\n' "${1:-OK}"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }

step "Checking this Mac"
curl -fsS --max-time 15 https://ollama.com >/dev/null || { echo "No internet connection."; exit 1; }
ok

step "Installing uv (manages Python for Click)"
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
ok

step "Installing Ollama (runs the AI models on this Mac)"
if [[ ! -d /Applications/Ollama.app ]] && ! command -v ollama >/dev/null; then
  curl -fL https://ollama.com/download/Ollama-darwin.zip -o /tmp/Ollama-darwin.zip
  ditto -x -k /tmp/Ollama-darwin.zip /Applications
fi
open -ga Ollama || true
OLLAMA="$(command -v ollama || echo /Applications/Ollama.app/Contents/Resources/ollama)"
for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break; sleep 1; done
ok

step "Installing the Click Companion (5-10 minutes)"
uv venv --python 3.12 "$VENV" --allow-existing
uv pip install --python "$PY" -r "$ROOT/requirements.txt" -r "$ROOT/requirements-mac.txt"
ok

step "Checking your hardware"
"$PY" "$SCRIPTS/print_models.py" --summary
IMAGES="$("$PY" "$SCRIPTS/print_models.py" --images)"
if [[ "$IMAGES" == "yes" ]]; then
  step "Installing image generation (large download, 10-30 minutes)"
  uv pip install --python "$PY" torch
  uv pip install --python "$PY" -r "$ROOT/requirements-images.txt"
  ok
else
  warn "Image generation is off on this Mac (it needs Apple Silicon)."
fi

step "Downloading the AI models for this Mac (large download)"
"$PY" "$SCRIPTS/print_models.py" | while read -r m; do [[ -n "$m" ]] && "$OLLAMA" pull "$m"; done
if [[ "$IMAGES" == "yes" ]]; then (cd "$ROOT" && "$PY" -m companion.imagegen --download); fi
ok

step "Starting Click Companion automatically when you log in"
PLIST="$HOME/Library/LaunchAgents/com.click.companion.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.click.companion</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>-m</string><string>companion</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/click-companion.log</string>
</dict></plist>
PL
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
ok

step "Done!"
echo "Look for the purple Click icon in the menu bar at the top of the screen."
echo
echo "macOS will ask for some permissions. Click needs these (System Settings > Privacy & Security):"
echo "  - Screen Recording   (to look at your screen when you ask)"
echo "  - Accessibility      (to point at buttons and click when you approve)"
echo "  - Input Monitoring   (for the Ctrl+Alt+Esc kill switch)"
echo "Turn on the entry called 'python3.12' (or 'Python') in each list, then quit and"
echo "reopen Click Companion from the menu bar icon (or log out and back in)."
echo
echo "Next: in the Click chat open Settings > Your computer > Pair a computer,"
echo "then click the menu bar icon > 'Pair with Click...' and type the code."
open "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture" || true
