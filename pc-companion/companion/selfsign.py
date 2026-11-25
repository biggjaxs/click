"""Click signs itself on YOUR Mac, so macOS keeps its permissions across updates.

macOS remembers permissions (Screen Recording, Accessibility) by an app's signature. Each Mac makes its
own signing key the first time Click is installed (kept in a private keychain inside Click's settings
folder; it never leaves this Mac), and every new version of Click is stamped with it when it's installed.
So each update looks like the same app to macOS, and no shared key has to be stored anywhere online.
"""
from __future__ import annotations

import logging
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger("click.selfsign")
NAME = "Click Local Signing"


def _run(*cmd, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=kw.pop("timeout", 120), **kw)


class Signer:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.keychain = self.folder / "click-signing.keychain-db"
        self.pw_file = self.folder / "click-signing.key"
        self.cert_file = self.folder / "click-signing-cert.pem"

    # ---- the identity (made once per Mac) ----------------------------------------------------------------
    def _password(self) -> str:
        if not self.pw_file.exists():
            self.pw_file.write_text(secrets.token_hex(24))
            os.chmod(self.pw_file, 0o600)
        return self.pw_file.read_text().strip()

    def identity(self) -> str:
        """The SHA-1 of this Mac's Click signing identity, creating it the first time."""
        pw = self._password()
        if not self.keychain.exists():
            r = _run("security", "create-keychain", "-p", pw, str(self.keychain))
            if r.returncode:
                raise RuntimeError(f"couldn't create the signing keychain: {r.stderr.strip()}")
            _run("security", "set-keychain-settings", str(self.keychain))  # never auto-lock
        _run("security", "unlock-keychain", "-p", pw, str(self.keychain))
        found = self._find()
        if found:
            self._ensure_trusted()
            return found
        tmp = Path(tempfile.mkdtemp())
        try:
            key, cert, p12 = tmp / "k.pem", tmp / "c.pem", tmp / "s.p12"
            r = _run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
                     "-days", "7300", "-subj", f"/CN={NAME}", "-addext", "basicConstraints=critical,CA:false",
                     "-addext", "keyUsage=critical,digitalSignature", "-addext", "extendedKeyUsage=critical,codeSigning")
            if r.returncode:
                raise RuntimeError(f"couldn't make a signing key: {r.stderr.strip()[:200]}")
            p12pw = secrets.token_hex(16)
            r = _run("openssl", "pkcs12", "-export", "-inkey", str(key), "-in", str(cert), "-out", str(p12),
                     "-passout", f"pass:{p12pw}", "-name", NAME)
            if r.returncode:
                raise RuntimeError(f"couldn't package the signing key: {r.stderr.strip()[:200]}")
            r = _run("security", "import", str(p12), "-k", str(self.keychain), "-P", p12pw, "-T", "/usr/bin/codesign")
            if r.returncode:
                raise RuntimeError(f"couldn't store the signing key: {r.stderr.strip()[:200]}")
            _run("security", "set-key-partition-list", "-S", "apple-tool:,apple:,codesign:", "-s", "-k", pw,
                 str(self.keychain))
            shutil.copy(cert, self.cert_file)  # the public half (not secret), needed to trust it
        finally:
            shutil.rmtree(tmp, ignore_errors=True)  # the key now only lives in Click's private keychain
        found = self._find()
        if not found:
            raise RuntimeError("the signing key was made but can't be found")
        self._ensure_trusted()
        return found

    def _valid(self) -> bool:
        r = _run("security", "find-identity", "-v", "-p", "codesigning", str(self.keychain))
        return NAME in r.stdout

    def _ensure_trusted(self) -> None:
        """macOS only signs with a key it trusts. Trusting this Mac's own key needs the Mac's password once
        (the standard macOS password box); after that every update is signed silently."""
        if self._valid():
            return
        if not self.cert_file.exists():
            raise RuntimeError("the signing certificate is missing")
        args = ["security", "add-trusted-cert", "-d", "-r", "trustRoot", "-p", "codeSign",
                "-k", "/Library/Keychains/System.keychain", str(self.cert_file)]
        if os.environ.get("CI"):
            r = _run("sudo", *args)
        else:
            cmd = " ".join("'" + a.replace("'", "'\\''") + "'" for a in args)
            prompt = "Click needs your password once, so future updates keep their permissions."
            r = _run("osascript", "-e", f'do shell script "{cmd}" with prompt "{prompt}" with administrator privileges',
                     timeout=300)
        if r.returncode or not self._valid():
            raise RuntimeError("the signing key wasn't trusted (password box cancelled?)")

    def _find(self) -> str | None:
        r = _run("security", "find-identity", "-p", "codesigning", str(self.keychain))
        for line in r.stdout.splitlines():
            if NAME in line:
                parts = line.split()
                if len(parts) >= 2 and len(parts[1]) == 40:
                    return parts[1]
        return None

    # ---- signing an app -------------------------------------------------------------------------------------
    @staticmethod
    def signed_by_us(app: Path) -> bool:
        r = _run("codesign", "-dvv", str(app))
        return f"Authority={NAME}" in (r.stderr + r.stdout)

    @staticmethod
    def _entitlements(app: Path) -> Path | None:
        bundled = app / "Contents" / "Resources" / "entitlements.plist"
        if bundled.exists():
            return bundled
        r = _run("codesign", "-d", "--entitlements", "-", "--xml", str(app))
        if r.returncode == 0 and "<plist" in r.stdout:
            f = Path(tempfile.mkstemp(suffix=".plist")[1])
            f.write_text(r.stdout[r.stdout.index("<?xml") if "<?xml" in r.stdout else r.stdout.index("<plist"):])
            return f
        return None

    def sign(self, app: Path) -> None:
        """Stamp a copy of Click (not the running one) with this Mac's identity, and check it."""
        ident = self.identity()
        _run("xattr", "-dr", "com.apple.quarantine", str(app))
        cmd = ["codesign", "--force", "--deep", "--timestamp=none", "-s", ident]
        ent = self._entitlements(app)
        if ent:
            cmd += ["--options", "runtime", "--entitlements", str(ent)]
        # codesign only finds keys in keychains on the search list: add Click's for a moment
        before = [ln.strip().strip('"') for ln in _run("security", "list-keychains", "-d", "user").stdout.splitlines()
                  if ln.strip()]
        if str(self.keychain) not in before:
            _run("security", "list-keychains", "-d", "user", "-s", *before, str(self.keychain))
        try:
            r = _run(*cmd, str(app), timeout=600)
        finally:
            if str(self.keychain) not in before and before:
                _run("security", "list-keychains", "-d", "user", "-s", *before)
        if r.returncode:
            diag = _run("security", "find-identity", "-v", "-p", "codesigning").stdout.strip()[-300:]
            raise RuntimeError(f"signing failed: {r.stderr.strip()[-300:]} | identities: {diag}")
        r = _run("codesign", "--verify", "--deep", str(app))
        if r.returncode:
            raise RuntimeError(f"the signed app didn't verify: {r.stderr.strip()[-300:]}")


def selftest(app: Path) -> int:
    """CI check: make an identity in a throwaway folder and sign a copy of the built app."""
    work = Path(tempfile.mkdtemp())
    copy = work / "Click.app"
    subprocess.run(["ditto", str(app), str(copy)], check=True)
    s = Signer(work)
    s.sign(copy)
    ok = Signer.signed_by_us(copy)
    if ok:  # and the stamped copy must still start
        env = {**os.environ, "HOME": str(work / "home"), "QT_QPA_PLATFORM": "offscreen"}
        (work / "home").mkdir()
        r = subprocess.run([str(copy / "Contents" / "MacOS" / "Click"), "--selftest"], capture_output=True, text=True,
                           timeout=240, env=env)
        ok = "SELFTEST OK" in (r.stdout + r.stderr)
        if not ok:
            print((r.stdout + r.stderr)[-2000:])
    print("SELFSIGN OK" if ok else "SELFSIGN FAILED")
    subprocess.run(["security", "delete-keychain", str(s.keychain)], capture_output=True)
    shutil.rmtree(work, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__" and len(sys.argv) > 1:
    raise SystemExit(selftest(Path(sys.argv[1])))
