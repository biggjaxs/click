#!/usr/bin/env bash
# Change the Click password (chat login + memory encryption key). Run on the Pi:
#     bash click/scripts/change-password.sh
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_DIR/pi-hub/.venv"
DB="/var/lib/click/click.db"
SECRETS_DIR="/etc/click"

sudo -v
while true; do
  read -r -s -p "New Click password: " P1; echo
  read -r -s -p "Type it again:      " P2; echo
  [[ "${#P1}" -ge 8 ]] || { echo "Too short (8+ characters)."; continue; }
  [[ "$P1" == "$P2" ]] || { echo "They don't match."; continue; }
  break
done
KEYS="$(CLICK_PASS="$P1" python3 -c '
import hashlib, os
p = os.environ["CLICK_PASS"].encode()
s1, s2 = os.urandom(16), os.urandom(16)
print(hashlib.scrypt(p, salt=s1, n=2**15, r=8, p=1, dklen=32, maxmem=64*1024*1024).hex())
print("scrypt$" + s2.hex() + "$" + hashlib.scrypt(p, salt=s2, n=2**14, r=8, p=1, dklen=32).hex())')"
unset P1 P2
NEW_KEY="$(echo "$KEYS" | sed -n 1p)"
NEW_UI="$(echo "$KEYS" | sed -n 2p)"
OLD_KEY="$(sudo cat "$SECRETS_DIR/db.key")"

echo "Stopping Click and re-encrypting memory with the new password..."
sudo systemctl stop click-hub
OLD_KEY="$OLD_KEY" NEW_KEY="$NEW_KEY" DB="$DB" "$VENV/bin/python" -c '
import os
from sqlcipher3 import dbapi2 as sqlite
c = sqlite.connect(os.environ["DB"])
c.execute("PRAGMA key = \"x%s\"" % repr(os.environ["OLD_KEY"]))
c.execute("SELECT count(*) FROM sqlite_master").fetchone()
c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
c.execute("PRAGMA rekey = \"x%s\"" % repr(os.environ["NEW_KEY"]))
c.close()
c = sqlite.connect(os.environ["DB"])
c.execute("PRAGMA key = \"x%s\"" % repr(os.environ["NEW_KEY"]))
c.execute("SELECT count(*) FROM sqlite_master").fetchone()
print("Memory re-encrypted.")'
echo "$NEW_KEY" | sudo tee "$SECRETS_DIR/db.key" >/dev/null
echo "$NEW_UI" | sudo tee "$SECRETS_DIR/ui.hash" >/dev/null
sudo chmod 600 "$SECRETS_DIR/db.key" "$SECRETS_DIR/ui.hash"
sudo systemctl start click-hub
echo "Done. Log in to the chat with your new password."
