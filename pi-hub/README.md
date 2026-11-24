# Pi Hub

Runs on the Raspberry Pi. It hosts the chat app, owns the encrypted memory, decides which tools to
use, and talks to the PC Companion. See `../docs/ARCHITECTURE.md` for the big picture.

## What it does
- Chat web app at `http://click.local`: streaming replies, markdown and code, uploads, inline
  images, panels for Gallery, Memory, Permissions, Activity log and Settings, light/dark, phone layout.
- Orchestrator with tool calling (web, images, screen, files, ghost cursor, actions, memory).
- Encrypted memory: SQLCipher plus numpy vector search over facts, episodes, messages and files.
- Backup model on the Pi (Ollama) for when the PC is off.
- Pairing and a mutual-TLS link to the PC.

## Install / run
Installed and started by `../scripts/install-pi.sh` as the `click-hub` systemd service.

```
sudo systemctl status click-hub
```

```
sudo journalctl -u click-hub -f
```

```
sudo systemctl restart click-hub
```

## Configuration
- `config/click.yaml`: models, budgets and timeouts (restart the service after changing it).
- The personality is edited in the chat (Settings → Personality and rules) and stored in
  `/var/lib/click/system_prompt.md`. `config/system_prompt.md` is the default.
- Environment overrides: `CLICK_DATA_DIR`, `CLICK_FALLBACK_MODEL`, `CLICK_EMBED_MODEL`,
  `CLICK_OLLAMA_URL`, `CLICK_PC_HOST`.

## Development (on any Linux/Mac machine)

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

```
.venv/bin/python -m pytest -q
```

To run it locally without the Pi's credentials, use `CLICK_DB_KEY=<64 hex characters>` (or
`CLICK_ALLOW_UNENCRYPTED=1` for throwaway testing) and `CLICK_DATA_DIR=/tmp/click`:

```
CLICK_DATA_DIR=/tmp/click CLICK_DB_KEY=$(python3 -c "print('ab'*32)") .venv/bin/uvicorn app.main:app --port 8080
```

## Troubleshooting
- **"Could not unlock the memory database":** `/etc/click/db.key` doesn't match the database.
  Restore your `/etc/click` backup.
- **Answers always come from the backup model:** check the Companion tray icon status, then
  Settings → Your computer → "Last error".
- **Slow backup answers:** normal on a Pi. Try `fallback_model: gemma3:270m` in `config/click.yaml`
  for speed.
