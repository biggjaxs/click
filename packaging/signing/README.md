# How Click is signed

No signing key is stored in this repository or in GitHub.

macOS remembers an app's permissions (Screen Recording, Accessibility) by its signature. So that every
update keeps those permissions, **each Mac stamps Click with its own signing key**: the first time Click is
installed, it creates a key that only exists on that Mac (in a private keychain inside
`~/Library/Application Support/ClickCompanion/`), and signs each new version with it when it is moved into
Applications. See `pc-companion/companion/selfsign.py`.

The build itself is signed ad-hoc. Gatekeeper still shows "Open Anyway" the first time, until Click is
signed with Apple's paid Developer ID.

An older shared key ("Click Code Signing") was used by earlier builds. It is retired; Click resets the
permissions that were granted to it once, so it can't be used to get them.
