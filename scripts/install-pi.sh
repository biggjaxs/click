#!/usr/bin/env bash
# =============================================================================
#  Click - Raspberry Pi installer
#
#  What this does (safe to run again at any time; it skips finished steps):
#    1. Updates the Pi and installs the system packages Click needs
#    2. Names the Pi "click" so you can reach it at http://click.local
#    3. Turns on a firewall that only allows your home network in
#    4. Turns off SSH password login (only if you already set up an SSH key)
#    5. Asks you for a password that locks Click's memory (encryption) and the chat
#    6. Creates Click's private data folder and Python environment
#    7. Installs Ollama, the small backup AI model and the memory-search model
#    8. Installs Click as a background service that starts on boot
#
#  Run it from your home folder on the Pi:
#      bash click/scripts/install-pi.sh
# =============================================================================
set -euo pipefail

# ---- Settings you can change ------------------------------------------------
CLICK_HOSTNAME="${CLICK_HOSTNAME:-click}"
FALLBACK_MODEL="${FALLBACK_MODEL:-gemma3:1b}"   # small backup model for when the PC is off
EMBED_MODEL="${EMBED_MODEL:-all-minilm}"        # tiny model for memory search
DATA_DIR="/var/lib/click"
SECRETS_DIR="/etc/click"
LAN_RANGES=("10.0.0.0/8" "172.16.0.0/12" "192.168.0.0/16")
# -----------------------------------------------------------------------------

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="$(id -un)"
VENV="$REPO_DIR/pi-hub/.venv"

green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
red()    { printf '\033[31m%s\033[0m\n' "$*"; }
step()   { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }

# ---- 0. Pre-flight checks ---------------------------------------------------
step "Checking this computer"
if [[ "$EUID" -eq 0 ]]; then
  red "Please run this as your normal user, not with sudo:"
  red "    bash click/scripts/install-pi.sh"
  exit 1
fi
if [[ "$(uname -m)" != "aarch64" ]]; then
  red "This installer is for a Raspberry Pi running 64-bit Raspberry Pi OS."
  red "Detected: $(uname -m). Re-flash with the 64-bit OS in Raspberry Pi Imager."
  exit 1
fi
MODEL_NAME="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
TOTAL_RAM_MB="$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)"
echo "Device: $MODEL_NAME"
echo "RAM:    ${TOTAL_RAM_MB} MB"
if (( TOTAL_RAM_MB < 3500 )); then
  yellow "Less than 4 GB RAM detected. Click will work, but the backup model may be slow."
fi
if ! curl -fsS --max-time 10 https://ollama.com >/dev/null; then
  red "No internet connection. Check the Pi's Wi-Fi or Ethernet and try again."
  exit 1
fi
green "OK"

echo "This installer needs your Pi password for system changes."
sudo -v
# keep sudo alive while the installer runs
while true; do sudo -n true; sleep 50; kill -0 "$$" 2>/dev/null || exit; done 2>/dev/null &

# ---- 1. System packages -----------------------------------------------------
step "Updating the Pi and installing packages (this can take 5-15 minutes)"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 python3-venv python3-pip python3-dev build-essential git curl ufw avahi-daemon \
  sqlite3 libsqlcipher-dev unattended-upgrades
sudo dpkg-reconfigure -f noninteractive unattended-upgrades
green "OK"

# ---- 2. Hostname -> http://click.local --------------------------------------
step "Naming this Pi '$CLICK_HOSTNAME'"
CURRENT_HOST="$(hostname)"
NEEDS_REBOOT=0
if [[ "$CURRENT_HOST" != "$CLICK_HOSTNAME" ]]; then
  sudo hostnamectl set-hostname "$CLICK_HOSTNAME"
  sudo sed -i "s/\b${CURRENT_HOST}\b/${CLICK_HOSTNAME}/g" /etc/hosts
  grep -q "$CLICK_HOSTNAME" /etc/hosts || echo "127.0.1.1 $CLICK_HOSTNAME" | sudo tee -a /etc/hosts >/dev/null
  NEEDS_REBOOT=1
fi
sudo systemctl enable --now avahi-daemon
green "OK - reachable as ${CLICK_HOSTNAME}.local"

# ---- 3. Firewall: home network only -----------------------------------------
step "Turning on the firewall (home network only)"
sudo ufw --force reset >/dev/null
sudo ufw default deny incoming
sudo ufw default allow outgoing
for net in "${LAN_RANGES[@]}"; do
  sudo ufw allow from "$net" to any port 22 proto tcp comment 'SSH (home network)'
  sudo ufw allow from "$net" to any port 80 proto tcp comment 'Click chat (home network)'
done
sudo ufw allow 5353/udp comment 'mDNS (click.local, finding your computer)'
sudo ufw --force enable
green "OK"

# ---- 4. SSH: turn off password login if a key is set up ---------------------
step "Securing SSH"
if [[ -s "$HOME/.ssh/authorized_keys" ]]; then
  printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\n' \
    | sudo tee /etc/ssh/sshd_config.d/10-click.conf >/dev/null
  sudo systemctl reload ssh
  green "OK - password login turned off; your SSH key is required now."
else
  printf 'PermitRootLogin no\n' | sudo tee /etc/ssh/sshd_config.d/10-click.conf >/dev/null
  sudo systemctl reload ssh
  yellow "Skipped turning off password login, because no SSH key is set up yet."
  yellow "(Doing it now would lock you out.) See 'Optional: SSH key' in the README,"
  yellow "then run this installer again."
fi

# ---- 5. Click password (memory encryption + chat login) ---------------------
step "Click password"
sudo mkdir -p "$SECRETS_DIR"
sudo chmod 700 "$SECRETS_DIR"
if sudo test -s "$SECRETS_DIR/db.key" && sudo test -s "$SECRETS_DIR/ui.hash"; then
  green "OK - already set (to change it later, see the README)."
else
  echo "Choose a password for Click. You'll type it to open the chat, and it locks"
  echo "Click's memory with encryption. Use at least 8 characters. Write it down!"
  while true; do
    read -r -s -p "Click password: " PASS1; echo
    read -r -s -p "Type it again:  " PASS2; echo
    if [[ "${#PASS1}" -lt 8 ]]; then red "Too short (8+ characters)."; continue; fi
    if [[ "$PASS1" != "$PASS2" ]]; then red "They don't match. Try again."; continue; fi
    break
  done
  # Derive the database key and the login hash (scrypt, separate random salts).
  KEYS="$(CLICK_PASS="$PASS1" python3 -c '
import hashlib, os
p = os.environ["CLICK_PASS"].encode()
s1, s2 = os.urandom(16), os.urandom(16)
key = hashlib.scrypt(p, salt=s1, n=2**15, r=8, p=1, dklen=32, maxmem=64*1024*1024).hex()
ui = "scrypt$" + s2.hex() + "$" + hashlib.scrypt(p, salt=s2, n=2**14, r=8, p=1, dklen=32).hex()
print(key); print(ui)')"
  unset PASS1 PASS2
  echo "$KEYS" | sed -n 1p | sudo tee "$SECRETS_DIR/db.key" >/dev/null
  echo "$KEYS" | sed -n 2p | sudo tee "$SECRETS_DIR/ui.hash" >/dev/null
  unset KEYS
  sudo chmod 600 "$SECRETS_DIR/db.key" "$SECRETS_DIR/ui.hash"
  sudo chown root:root "$SECRETS_DIR/db.key" "$SECRETS_DIR/ui.hash"
  green "OK - password set."
fi

# ---- 6. Data folder + Python environment ------------------------------------
step "Creating Click's private data folder and Python environment (5-10 minutes)"
sudo mkdir -p "$DATA_DIR"
sudo chown "$RUN_USER:$RUN_USER" "$DATA_DIR"
chmod 700 "$DATA_DIR"
[[ -d "$VENV" ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip wheel
"$VENV/bin/pip" install --quiet -r "$REPO_DIR/pi-hub/requirements.txt"
"$VENV/bin/python" -c "from sqlcipher3 import dbapi2" \
  || { red "SQLCipher (memory encryption) failed to install. Send me the messages above."; exit 1; }
green "OK - data lives in $DATA_DIR (only you can read it)"

# ---- 7. Ollama + models -----------------------------------------------------
step "Installing Ollama, the backup model ($FALLBACK_MODEL) and the memory model ($EMBED_MODEL)"
if ! command -v ollama >/dev/null; then
  curl -fsSL https://ollama.com/install.sh | sh
fi
# Ollama listens on 127.0.0.1 only by default, so it is never open to the network.
sudo systemctl enable --now ollama
for _ in {1..30}; do
  curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break
  sleep 1
done
ollama pull "$FALLBACK_MODEL"
ollama pull "$EMBED_MODEL"
green "OK"

# ---- 8. Click background service --------------------------------------------
step "Installing the Click service"
sed -e "s|@USER@|$RUN_USER|g" \
    -e "s|@REPO@|$REPO_DIR|g" \
    -e "s|@DATA@|$DATA_DIR|g" \
    -e "s|@MODEL@|$FALLBACK_MODEL|g" \
    -e "s|@EMBED@|$EMBED_MODEL|g" \
    -e "s|@SECRETS@|$SECRETS_DIR|g" \
    "$REPO_DIR/scripts/systemd/click-hub.service" \
  | sudo tee /etc/systemd/system/click-hub.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable click-hub
sudo systemctl restart click-hub
for _ in {1..30}; do
  curl -fsS http://127.0.0.1/login >/dev/null 2>&1 && break
  sleep 1
done
if curl -fsS http://127.0.0.1/login >/dev/null 2>&1; then
  green "OK - Click is running"
else
  red "Click didn't start. Show the error with:  sudo journalctl -u click-hub -n 50"
fi

# ---- Summary ----------------------------------------------------------------
IP="$(hostname -I | awk '{print $1}')"
step "Done!"
echo "Device ......... $MODEL_NAME (${TOTAL_RAM_MB} MB RAM)"
echo "Chat ........... http://${CLICK_HOSTNAME}.local   (or http://$IP)"
echo "Firewall ....... $(sudo ufw status | head -1)"
echo "Ollama ......... $(ollama --version 2>/dev/null | tail -1)"
echo "Backup model ... $(ollama list | grep -q "${FALLBACK_MODEL%%:*}" && echo "$FALLBACK_MODEL installed" || echo MISSING)"
echo "Memory model ... $(ollama list | grep -q "$EMBED_MODEL" && echo "$EMBED_MODEL installed" || echo MISSING)"
echo "Click service .. $(systemctl is-active click-hub)"
echo
echo "Next: open the chat address above in your computer's browser and log in with"
echo "your Click password. Then install the Click Companion on your computer."
if [[ "$NEEDS_REBOOT" == "1" ]]; then
  echo
  yellow "The Pi's name changed. Restarting in 10 seconds so it takes effect..."
  yellow "Reconnect afterwards with:  ssh ${RUN_USER}@${CLICK_HOSTNAME}.local"
  sleep 10
  sudo reboot
fi
