# Shared

The Pi <-> PC protocol, used by both sides so they always agree.

- `click_protocol/__init__.py`: message envelope (`make_message`, `validate`, `reply`,
  `error_reply`) and constants (mDNS type `_click._tcp`, port 47321, heartbeat timing, size limit).
- `click_protocol/tls.py`: self-signed identities, fingerprints, and pinned mutual-TLS contexts.
- `schema/message.schema.json`: JSON schema of the envelope.

The list of services and actions is in `../docs/ARCHITECTURE.md`.
