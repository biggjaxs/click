"""Long-term memory about the user.

Four layers (build spec, Feature 5):
1. Conversation history  - the `messages` table (searchable)
2. Profile facts         - the `facts` table, with history via `superseded_by`
3. Episodic notes        - short summaries per session (`episodes`)
4. Semantic index        - embeddings of facts, episodes, messages and files (`embeddings`)

Everything stays on the Pi in the encrypted database.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from typing import Awaitable, Callable

import numpy as np

from .audit import AuditLog
from .db import Database

log = logging.getLogger("click.memory")

CATEGORIES = ["identity", "work", "projects", "people", "preferences", "routines",
              "tools", "health", "money", "other"]
CORE_CATEGORIES = ("identity", "preferences")
DUPLICATE_SIM = 0.92
MIN_CONFIDENCE = 0.6

# --- Never save: passwords, card numbers, bank details, ID numbers, one-time codes -----------
_SENSITIVE = [
    re.compile(r"\b(?:\d[ -]?){13,19}\b"),                                   # card-like numbers
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                                      # US SSN
    re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),                          # IBAN
    re.compile(r"\b(password|passcode|passwd|pin|cvv|cvc|security code|one[- ]time code|otp|"
               r"verification code|2fa code|routing number|account number|sort code|social security|"
               r"passport number|driver'?s licen[cs]e number|api key|secret key|private key|seed phrase|"
               r"recovery phrase)\b", re.I),
    re.compile(r"\b(sk|pk|ghp|gho|xox[abp])[-_][A-Za-z0-9_-]{16,}\b"),     # API tokens
    re.compile(r"\b[A-Fa-f0-9]{40,}\b"),                                      # long hex secrets
]


def looks_sensitive(text: str) -> bool:
    for pattern in _SENSITIVE:
        for m in pattern.finditer(text):
            if pattern is _SENSITIVE[0]:
                digits = re.sub(r"\D", "", m.group())
                if _luhn_ok(digits):
                    return True
                continue
            return True
    return False


def _luhn_ok(digits: str) -> bool:
    if not 13 <= len(digits) <= 19:
        return False
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _vec_to_blob(vec) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def _blob_to_vec(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def _keyword_score(a: str, b: str) -> float:
    ta = set(re.findall(r"[a-z0-9]{3,}", a.lower()))
    tb = set(re.findall(r"[a-z0-9]{3,}", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / math.sqrt(len(ta) * len(tb))


Embedder = Callable[[list[str]], Awaitable[list[list[float]]]]


class Memory:
    def __init__(self, db: Database, audit: AuditLog, embedder: Embedder | None):
        self.db = db
        self.audit = audit
        self.embedder = embedder

    # ---- settings ---------------------------------------------------------------------
    @property
    def blocked_categories(self) -> set[str]:
        return set(self.db.get_setting("memory_blocked_categories", ["health", "money"]))

    def set_blocked_categories(self, cats: list[str]) -> None:
        self.db.set_setting("memory_blocked_categories", [c for c in cats if c in CATEGORIES])

    # ---- embeddings ---------------------------------------------------------------------
    async def _embed(self, texts: list[str]) -> list[np.ndarray] | None:
        # on processor-only computers, memory search uses keywords: a separate embedding model would push the chat
        # models out of memory (only 3 fit at once) and they'd have to reload
        if not self.embedder or not texts or getattr(self, "lexical", False):
            return None
        try:
            vecs = await self.embedder(texts)
            out = []
            for v in vecs:
                arr = np.asarray(v, dtype=np.float32)
                n = np.linalg.norm(arr)
                out.append(arr / n if n else arr)
            return out
        except Exception as exc:  # noqa: BLE001 - embeddings are best-effort
            log.warning("embedding failed: %s", exc)
            return None

    async def index(self, kind: str, ref_id: int, text: str) -> None:
        vecs = await self._embed([text])
        if vecs is None:
            return
        self.db.execute(
            "INSERT INTO embeddings(kind, ref_id, text, vector, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(kind, ref_id) DO UPDATE SET text=excluded.text, vector=excluded.vector",
            (kind, ref_id, text[:2000], _vec_to_blob(vecs[0]), time.time()),
        )

    def _unindex(self, kind: str, ref_id: int) -> None:
        self.db.execute("DELETE FROM embeddings WHERE kind = ? AND ref_id = ?", (kind, ref_id))

    async def search(self, query: str, kinds: tuple[str, ...], limit: int = 20,
                     exclude_conversation: int | None = None) -> list[dict]:
        """Rank stored items by meaning (or keywords if embeddings are unavailable)."""
        placeholders = ",".join("?" * len(kinds))
        rows = self.db.query(
            f"SELECT kind, ref_id, text, vector, created_at FROM embeddings WHERE kind IN ({placeholders})",
            kinds,
        )
        qv = await self._embed([query])
        results = []
        excluded_msgs = set()
        if exclude_conversation is not None:
            excluded_msgs = {r["id"] for r in self.db.query(
                "SELECT id FROM messages WHERE conversation_id = ?", (exclude_conversation,))}
        if rows and qv is not None:
            mat = np.stack([_blob_to_vec(r["vector"]) for r in rows])
            if mat.shape[1] == qv[0].shape[0]:
                sims = mat @ qv[0]
                for r, s in zip(rows, sims):
                    results.append({**{k: r[k] for k in ("kind", "ref_id", "text", "created_at")}, "sim": float(s)})
        if not results:  # keyword fallback over facts/episodes/messages
            results = self._keyword_candidates(query, kinds)
        results = [r for r in results if not (r["kind"] == "message" and r["ref_id"] in excluded_msgs)]
        results.sort(key=lambda r: r["sim"], reverse=True)
        return results[:limit]

    def _keyword_candidates(self, query: str, kinds) -> list[dict]:
        out = []
        if "fact" in kinds:
            for f in self.active_facts():
                out.append({"kind": "fact", "ref_id": f["id"], "text": f["text"], "created_at": f["updated_at"],
                            "sim": _keyword_score(query, f["text"])})
        if "episode" in kinds:
            for e in self.db.query("SELECT id, summary, created_at FROM episodes ORDER BY id DESC LIMIT 300"):
                out.append({"kind": "episode", "ref_id": e["id"], "text": e["summary"], "created_at": e["created_at"],
                            "sim": _keyword_score(query, e["summary"])})
        if "message" in kinds:
            for m in self.db.query("SELECT id, content, created_at FROM messages WHERE role IN ('user','assistant') "
                                   "ORDER BY id DESC LIMIT 500"):
                out.append({"kind": "message", "ref_id": m["id"], "text": m["content"][:500],
                            "created_at": m["created_at"], "sim": _keyword_score(query, m["content"])})
        if "screen" in kinds:
            for m in self.db.query("SELECT id, app, title, text, created_at FROM screen_history ORDER BY id DESC "
                                   "LIMIT 3000"):
                t = f"{m['app']} - {m['title']}\n{m['text'][:600]}"
                out.append({"kind": "screen", "ref_id": m["id"], "text": t, "created_at": m["created_at"],
                            "sim": _keyword_score(query, t)})
        return [r for r in out if r["sim"] > 0]

    # ---- facts ------------------------------------------------------------------------------
    def active_facts(self) -> list[dict]:
        return self.db.query("SELECT * FROM facts WHERE superseded_by IS NULL ORDER BY updated_at DESC")

    def list_facts(self, q: str | None = None, include_history: bool = False) -> list[dict]:
        sql = "SELECT * FROM facts"
        where, params = [], []
        if not include_history:
            where.append("superseded_by IS NULL")
        if q:
            where.append("(text LIKE ? OR category LIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
        if where:
            sql += " WHERE " + " AND ".join(where)
        return self.db.query(sql + " ORDER BY updated_at DESC", params)

    def fact_history(self, fact_id: int) -> list[dict]:
        """Older versions that this fact replaced ("Works at X, previously Y")."""
        chain, current = [], fact_id
        for _ in range(20):
            prev = self.db.one("SELECT * FROM facts WHERE superseded_by = ?", (current,))
            if not prev:
                break
            chain.append(prev)
            current = prev["id"]
        return chain

    async def add_fact(self, text: str, category: str = "other", confidence: float = 0.7,
                       source_type: str = "chat", source_ref: str | None = None,
                       replaces: int | None = None, explicit: bool = False) -> dict:
        """Save a fact. Returns {"status": saved|duplicate|updated|blocked, ...}."""
        text = " ".join(text.split())[:500]
        category = category if category in CATEGORIES else "other"
        if not text:
            return {"status": "blocked", "reason": "empty"}
        if looks_sensitive(text):
            self.audit.log("memory", "blocked_sensitive", "A fact looked like a password/card/ID and was not saved")
            return {"status": "blocked", "reason": "sensitive"}
        if category in self.blocked_categories and not explicit:
            return {"status": "blocked", "reason": f"category '{category}' is turned off"}
        now = time.time()
        # Duplicate check by meaning
        vecs = await self._embed([text])
        if vecs is not None:
            for r in await self.search(text, ("fact",), limit=3):
                if r["sim"] >= DUPLICATE_SIM:
                    existing = self.db.one("SELECT * FROM facts WHERE id = ? AND superseded_by IS NULL", (r["ref_id"],))
                    if existing:
                        self.db.execute("UPDATE facts SET confidence = MAX(confidence, ?), updated_at = ? WHERE id = ?",
                                        (confidence, now, existing["id"]))
                        return {"status": "duplicate", "id": existing["id"], "text": existing["text"]}
        else:
            for f in self.active_facts():
                if f["text"].lower() == text.lower():
                    return {"status": "duplicate", "id": f["id"], "text": f["text"]}
        fact_id = self.db.execute(
            "INSERT INTO facts(text, category, confidence, source_type, source_ref, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (text, category, float(confidence), source_type, source_ref, now, now),
        )
        status = "saved"
        if replaces:
            old = self.db.one("SELECT * FROM facts WHERE id = ? AND superseded_by IS NULL", (replaces,))
            if old:
                self.db.execute("UPDATE facts SET superseded_by = ? WHERE id = ?", (fact_id, replaces))
                self._unindex("fact", replaces)
                status = "updated"
                self.audit.log("memory", "fact_updated", f"'{old['text']}' -> '{text}'")
        if status == "saved":
            self.audit.log("memory", "fact_saved", f"[{category}] {text} (from {source_type})")
        if vecs is not None:
            self.db.execute(
                "INSERT OR REPLACE INTO embeddings(kind, ref_id, text, vector, created_at) VALUES ('fact', ?, ?, ?, ?)",
                (fact_id, text, _vec_to_blob(vecs[0]), now),
            )
        return {"status": status, "id": fact_id, "text": text, "category": category}

    async def edit_fact(self, fact_id: int, text: str, category: str | None = None) -> dict | None:
        fact = self.db.one("SELECT * FROM facts WHERE id = ?", (fact_id,))
        if not fact:
            return None
        if looks_sensitive(text):
            raise ValueError("That looks like sensitive information (password, card or ID number), so it can't be saved.")
        cat = category if category in CATEGORIES else fact["category"]
        self.db.execute("UPDATE facts SET text = ?, category = ?, confidence = 1.0, updated_at = ? WHERE id = ?",
                        (text, cat, time.time(), fact_id))
        await self.index("fact", fact_id, text)
        self.audit.log("memory", "fact_edited", f"#{fact_id}: {text}")
        return self.db.one("SELECT * FROM facts WHERE id = ?", (fact_id,))

    def delete_fact(self, fact_id: int) -> bool:
        fact = self.db.one("SELECT * FROM facts WHERE id = ?", (fact_id,))
        if not fact:
            return False
        # Delete the fact plus its older versions (anything it was derived from).
        ids = [fact_id] + [f["id"] for f in self.fact_history(fact_id)]
        for i in ids:
            self._unindex("fact", i)
        self.db.execute(f"DELETE FROM facts WHERE id IN ({','.join('?' * len(ids))})", ids)
        self.audit.log("memory", "fact_deleted", fact["text"])
        return True

    async def forget(self, about: str) -> list[str]:
        """'Forget that ...': delete matching facts and anything derived from them."""
        matches = await self.search(about, ("fact",), limit=10)
        threshold = 0.55 if self.embedder else 0.3
        removed = []
        for m in matches:
            if m["sim"] >= threshold:
                fact = self.db.one("SELECT text FROM facts WHERE id = ?", (m["ref_id"],))
                if fact and self.delete_fact(m["ref_id"]):
                    removed.append(fact["text"])
        # also substring matches
        for f in self.active_facts():
            if about.lower().strip(" .") in f["text"].lower() and self.delete_fact(f["id"]):
                removed.append(f["text"])
        return removed

    # ---- recall ---------------------------------------------------------------------------------
    def core_profile(self, limit: int = 8) -> list[str]:
        rows = self.db.query(
            f"SELECT text FROM facts WHERE superseded_by IS NULL AND category IN ({','.join('?' * len(CORE_CATEGORIES))}) "
            "ORDER BY confidence DESC, updated_at DESC LIMIT ?",
            (*CORE_CATEGORIES, limit),
        )
        return [r["text"] for r in rows]

    async def recall(self, query: str, budget_tokens: int, conversation_id: int | None = None) -> dict:
        core = self.core_profile()
        used = sum(estimate_tokens(c) for c in core)
        lexical = getattr(self, "lexical", False)
        kinds = ("fact",) if lexical else ("fact", "episode", "message", "file")  # keyword mode: saved facts only
        candidates = await self.search(query, kinds, limit=40, exclude_conversation=conversation_id)
        now = time.time()
        scored = []
        for c in candidates:
            if c["kind"] == "fact" and c["text"] in core:
                continue
            age_days = max(0.0, (now - (c["created_at"] or now)) / 86400)
            recency = math.exp(-age_days / 30)
            importance = 1.0 if c["kind"] == "fact" else 0.6 if c["kind"] == "episode" else 0.4
            score = 0.7 * c["sim"] + 0.15 * recency + 0.15 * importance
            if c["sim"] >= (0.4 if lexical else 0.35 if self.embedder else 0.15):
                scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        relevant = []
        for _, c in scored:
            cost = estimate_tokens(c["text"])
            if used + cost > budget_tokens:
                continue
            used += cost
            relevant.append({"kind": c["kind"], "text": c["text"]})
            if len(relevant) >= (3 if lexical else 12):
                break
        return {"core": core, "relevant": relevant}

    # ---- extraction --------------------------------------------------------------------------------
    EXTRACT_PROMPT = (
        "You extract durable facts about the USER from a chat exchange, for a personal assistant's memory.\n"
        "Only facts about the user themselves: name, job, businesses, projects, people they mention, "
        "preferences, routines, tools they use, how they like answers formatted.\n"
        "Do NOT extract: passwords, card/bank/ID numbers, codes, one-off requests, facts about the world, "
        "or things the assistant said. Do not build profiles of other people beyond what the user said "
        "about their relationship to them.\n"
        "Categories: " + ", ".join(CATEGORIES) + ".\n"
        "If a new fact updates one of the EXISTING facts below, set \"replaces\" to that fact's id.\n"
        'Answer ONLY with JSON: {"facts": [{"fact": "...", "category": "...", "confidence": 0.0-1.0, '
        '"replaces": null}]}. Use {"facts": []} if there is nothing worth remembering.'
    )

    async def extract_from_exchange(self, backend, user_text: str, assistant_text: str,
                                    source_message_id: int, source_type: str = "chat") -> list[dict]:
        from .llm import complete

        related = await self.search(user_text, ("fact",), limit=6)
        existing = "\n".join(f"#{r['ref_id']}: {r['text']}" for r in related) or "(none)"
        prompt = [
            {"role": "system", "content": self.EXTRACT_PROMPT},
            {"role": "user", "content": f"EXISTING facts:\n{existing}\n\nUSER said:\n{user_text[:3000]}\n\n"
                                        f"ASSISTANT replied:\n{assistant_text[:1500]}"},
        ]
        raw = await complete(backend, prompt, fmt="json")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.S)
            if not m:
                return []
            try:
                data = json.loads(m.group())
            except json.JSONDecodeError:
                return []
        saved = []
        valid_ids = {r["ref_id"] for r in related}
        for item in (data.get("facts") or [])[:8]:
            if not isinstance(item, dict) or not item.get("fact"):
                continue
            conf = float(item.get("confidence") or 0.7)
            if conf < MIN_CONFIDENCE:
                continue
            replaces = item.get("replaces")
            replaces = int(replaces) if isinstance(replaces, (int, str)) and str(replaces).isdigit() else None
            if replaces not in valid_ids:
                replaces = None
            res = await self.add_fact(item["fact"], item.get("category", "other"), conf,
                                      source_type, f"message:{source_message_id}", replaces=replaces)
            if res["status"] in ("saved", "updated"):
                saved.append(res)
        return saved

    # ---- episodes and consolidation ------------------------------------------------------------------
    async def summarize_conversation(self, backend, conversation_id: int) -> str | None:
        from .llm import complete

        conv = self.db.one("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
        if not conv:
            return None
        msgs = self.db.query(
            "SELECT id, role, content FROM messages WHERE conversation_id = ? AND id > ? "
            "AND role IN ('user','assistant') ORDER BY id",
            (conversation_id, conv["last_episode_msg_id"]),
        )
        if len(msgs) < 2:
            return None
        transcript = "\n".join(f"{m['role'].upper()}: {m['content'][:600]}" for m in msgs)[-8000:]
        summary = (await complete(backend, [
            {"role": "system", "content": "Summarize what happened in this chat session in ONE short sentence "
                                          "(max 25 words), e.g. 'Helped debug the invoice script; fixed a date bug.' "
                                          "Never include passwords, card or ID numbers."},
            {"role": "user", "content": transcript},
        ])).strip().strip('"')[:300]
        if not summary or looks_sensitive(summary):
            return None
        ep_id = self.db.execute("INSERT INTO episodes(conversation_id, summary, created_at) VALUES (?, ?, ?)",
                                (conversation_id, summary, time.time()))
        self.db.execute("UPDATE conversations SET last_episode_msg_id = ? WHERE id = ?",
                        (msgs[-1]["id"], conversation_id))
        await self.index("episode", ep_id, summary)
        self.audit.log("memory", "episode_saved", summary)
        return summary

    async def consolidate(self, backend) -> dict:
        """Weekly: merge duplicates, summarize old chats, drop stale low-value facts."""
        merged = dropped = summarized = 0
        facts = self.active_facts()
        texts = [f["text"] for f in facts]
        vecs = await self._embed(texts) if texts else None
        if vecs is not None and len(facts) > 1:
            mat = np.stack(vecs)
            sims = mat @ mat.T
            removed: set[int] = set()
            for i in range(len(facts)):
                if facts[i]["id"] in removed:
                    continue
                for j in range(i + 1, len(facts)):
                    if facts[j]["id"] not in removed and sims[i, j] >= DUPLICATE_SIM:
                        # keep the newer (facts are sorted newest first) and supersede the older
                        self.db.execute("UPDATE facts SET superseded_by = ? WHERE id = ?", (facts[i]["id"], facts[j]["id"]))
                        self._unindex("fact", facts[j]["id"])
                        removed.add(facts[j]["id"])
                        merged += 1
        cutoff = time.time() - 90 * 86400
        for f in self.db.query("SELECT id FROM facts WHERE superseded_by IS NULL AND confidence < 0.7 AND updated_at < ?",
                               (cutoff,)):
            self.delete_fact(f["id"])
            dropped += 1
        old_convs = self.db.query(
            "SELECT c.id FROM conversations c WHERE c.updated_at < ? AND EXISTS "
            "(SELECT 1 FROM messages m WHERE m.conversation_id = c.id AND m.id > c.last_episode_msg_id)",
            (time.time() - 86400,),
        )
        for c in old_convs[:20]:
            if await self.summarize_conversation(backend, c["id"]):
                summarized += 1
        self.db.set_setting("last_consolidation", time.time())
        result = {"merged": merged, "dropped": dropped, "summarized": summarized}
        self.audit.log("memory", "consolidated", json.dumps(result))
        return result

    # ---- export / wipe ---------------------------------------------------------------------------------
    def export_all(self) -> dict:
        return {
            "exported_at": time.time(),
            "facts": self.db.query("SELECT * FROM facts ORDER BY id"),
            "episodes": self.db.query("SELECT * FROM episodes ORDER BY id"),
            "conversations": self.db.query("SELECT * FROM conversations ORDER BY id"),
            "messages": self.db.query("SELECT id, conversation_id, role, content, meta, created_at FROM messages ORDER BY id"),
        }

    def wipe_all(self) -> None:
        for table in ("embeddings", "facts", "episodes"):
            self.db.execute(f"DELETE FROM {table}")
        self.audit.log("memory", "wiped", "All memories deleted")
