# Click's signing certificate

A self-made code-signing certificate ("Click Code Signing"). Every Mac build is signed with it so
macOS sees each new version as the **same app** and keeps its permissions (Screen Recording,
Accessibility, Input Monitoring) across updates. It does NOT make macOS trust the app (that needs
Apple's paid Developer ID), so the first open still needs "Open Anyway".

**Security note:** the private key is in `click-signing.p12` (password `click`) because this repo
is private and this session can't set GitHub secrets. Anyone who can read this repo could sign an
app that macOS treats as Click. Before going public: move it to a GitHub Actions secret, or better,
switch to an Apple Developer ID certificate.
