"""Certificates and mutual-TLS contexts for the Pi <-> PC link.

Each side creates its own self-signed certificate once. During pairing they swap certificates,
and from then on each side only trusts the exact certificate it was given ("pinning").
"""
from __future__ import annotations

import datetime as dt
import hashlib
import ssl
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def generate_identity(common_name: str) -> tuple[str, str]:
    """Return (cert_pem, key_pem) for a new self-signed identity valid for 20 years."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365 * 20))
        # Self-signed and used as its own trust anchor when pinned by the other side.
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=False,
                content_commitment=False, key_encipherment=False, data_encipherment=False,
                key_agreement=True, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(common_name)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return cert_pem, key_pem


def fingerprint(cert_pem: str) -> str:
    """SHA-256 fingerprint of a PEM certificate, as colon-separated hex (short form shown to users)."""
    der = x509.load_pem_x509_certificate(cert_pem.encode()).public_bytes(serialization.Encoding.DER)
    return hashlib.sha256(der).hexdigest()


def _load_chain(ctx: ssl.SSLContext, cert_pem: str, key_pem: str) -> None:
    # ssl needs files; write to a private temp dir that is removed right away.
    with tempfile.TemporaryDirectory() as tmp:
        cert_path = Path(tmp) / "c.pem"
        key_path = Path(tmp) / "k.pem"
        cert_path.write_text(cert_pem)
        key_path.write_text(key_pem)
        key_path.chmod(0o600)
        ctx.load_cert_chain(cert_path, key_path)


def server_context(own_cert: str, own_key: str, trusted_peer_cert: str | None) -> ssl.SSLContext:
    """TLS context for the PC Companion's server. Requires the pinned Pi certificate."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    _load_chain(ctx, own_cert, own_key)
    if trusted_peer_cert:
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(cadata=trusted_peer_cert)
    else:
        # Not paired yet: refuse every client certificate by trusting nothing.
        ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def client_context(own_cert: str, own_key: str, trusted_peer_cert: str) -> ssl.SSLContext:
    """TLS context for the Pi connecting to the PC. Only the pinned PC certificate is trusted."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False  # PCs are reached by changing IP addresses; trust is the pinned cert.
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cadata=trusted_peer_cert)
    _load_chain(ctx, own_cert, own_key)
    return ctx
