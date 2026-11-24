"""Web search from the Pi. Only the search words leave the box (no account, no API key, no cloud AI)."""
from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
from urllib.parse import urlparse

import httpx

from .memory import looks_sensitive

USER_AGENT = "Mozilla/5.0 (X11; Linux aarch64) Click/1.0"


def scrub_query(query: str) -> str:
    """Refuse queries that contain secrets; trim to a sane length."""
    query = " ".join(query.split())[:300]
    if looks_sensitive(query):
        raise ValueError("That search contains something that looks private (a password, card or ID number), "
                         "so I won't send it to a search engine.")
    return query


async def web_search(query: str, max_results: int = 5) -> list[dict]:
    query = scrub_query(query)

    def _run():
        try:
            from ddgs import DDGS
        except ImportError:  # older package name
            from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))

    results = await asyncio.to_thread(_run)
    return [{"title": r.get("title", ""), "url": r.get("href") or r.get("url", ""), "snippet": r.get("body", "")}
            for r in results]


def _is_public_host(host: str) -> bool:
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except (socket.gaierror, ValueError):
        return False


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|svg|header|footer|nav)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h[1-6]>|</li>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


async def fetch_page(url: str, max_chars: int = 6000) -> dict:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Only http(s) web pages can be opened.")
    if not await asyncio.to_thread(_is_public_host, parsed.hostname):
        raise ValueError("For safety I only open public web pages, not addresses on your home network.")
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        r = await client.get(url)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype:
            raise ValueError(f"That page is not text ({ctype}).")
        text = html_to_text(r.text) if "html" in ctype else r.text
    return {"url": str(r.url), "text": text[:max_chars], "truncated": len(text) > max_chars}
