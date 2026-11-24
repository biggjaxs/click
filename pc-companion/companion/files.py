"""Files service: read-only access to approved folders, plus rename/move with permission."""
from __future__ import annotations

import base64
import csv
import fnmatch
import io
import os
import time
from pathlib import Path

TEXT_EXTS = {".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
             ".html", ".css", ".xml", ".sh", ".ps1", ".bat", ".c", ".h", ".cpp", ".hpp", ".java", ".cs", ".go", ".rs",
             ".rb", ".php", ".sql", ".log", ".swift", ".kt", ".lua", ".r", ".tex", ".csv"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
MAX_READ = 25 * 1024 * 1024
MAX_CHARS = 60_000
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "Library", "AppData", ".cache"}


class FileAccessError(PermissionError):
    pass


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    data = path.read_bytes()
    if ext == ".csv":
        rows = list(csv.reader(io.StringIO(data.decode("utf-8", errors="replace"))))
        return "\n".join(", ".join(r) for r in rows[:2000])[:MAX_CHARS]
    if ext in TEXT_EXTS:
        return data.decode("utf-8", errors="replace")[:MAX_CHARS]
    if ext == ".pdf":
        from pypdf import PdfReader

        return "\n\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages[:200])[:MAX_CHARS]
    if ext == ".docx":
        import docx

        doc = docx.Document(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs]
        for t in doc.tables:
            parts += [" | ".join(c.text for c in row.cells) for row in t.rows]
        return "\n".join(parts)[:MAX_CHARS]
    if ext in (".xlsx", ".xlsm"):
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        parts = []
        for ws in wb.worksheets[:10]:
            parts.append(f"## Sheet: {ws.title}")
            parts += [", ".join("" if v is None else str(v) for v in row)
                      for row in ws.iter_rows(max_row=2000, values_only=True) if any(v is not None for v in row)]
        return "\n".join(parts)[:MAX_CHARS]
    raise FileAccessError(f"Click can't read {ext or 'this'} files yet.")


class FileService:
    def __init__(self, settings):
        self.settings = settings

    @property
    def roots(self) -> list[Path]:
        return [Path(p).expanduser().resolve() for p in self.settings["approved_folders"] if Path(p).expanduser().exists()]

    def resolve(self, path: str) -> Path:
        """Return a real path inside an approved folder, or refuse."""
        if not path:
            raise FileAccessError("No path given.")
        raw = Path(path).expanduser()
        candidates = [raw] if raw.is_absolute() else [root / raw for root in self.roots] + \
            [root.parent / raw for root in self.roots]
        for cand in candidates:
            real = cand.resolve()
            for root in self.roots:
                if real == root or real.is_relative_to(root):
                    if real.exists():
                        return real
        names = ", ".join(r.name for r in self.roots) or "none"
        raise FileAccessError(f"'{path}' isn't in an approved folder (approved: {names}). "
                              "Folders can be added in the Click Companion settings.")

    def _info(self, p: Path) -> dict:
        st = p.stat()
        return {"name": p.name, "path": str(p), "type": "folder" if p.is_dir() else "file",
                "size": st.st_size if p.is_file() else None,
                "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))}

    def list_folder(self, path: str = "", **_) -> dict:
        if not path or path in (".", "/", "~"):
            return {"approved_folders": [self._info(r) for r in self.roots]}
        p = self.resolve(path)
        if not p.is_dir():
            raise FileAccessError(f"{p.name} is not a folder.")
        entries = sorted((e for e in p.iterdir() if not e.name.startswith(".")),
                         key=lambda e: (not e.is_dir(), e.name.lower()))
        return {"folder": str(p), "entries": [self._info(e) for e in entries[:500]],
                "truncated": len(entries) > 500}

    def read_file(self, path: str, **_) -> dict:
        p = self.resolve(path)
        if not p.is_file():
            raise FileAccessError(f"{p.name} is not a file.")
        if p.stat().st_size > MAX_READ:
            raise FileAccessError("That file is larger than 25 MB.")
        if p.suffix.lower() in IMAGE_EXTS:
            from PIL import Image

            img = Image.open(p).convert("RGB")
            img.thumbnail((1600, 1600))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=85)
            return {"path": str(p), "image_b64": base64.b64encode(buf.getvalue()).decode(), "mime": "image/jpeg"}
        text = extract_text(p)
        return {"path": str(p), "text": text, "truncated": len(text) >= MAX_CHARS}

    def _walk(self, folder: Path, max_files: int = 20000):
        count = 0
        for dirpath, dirnames, filenames in os.walk(folder):
            depth = len(Path(dirpath).relative_to(folder).parts)
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS and depth < 6]
            for f in filenames:
                if f.startswith("."):
                    continue
                count += 1
                if count > max_files:
                    return
                yield Path(dirpath) / f

    def search_files(self, query: str, by: str = "name", folder: str | None = None, **_) -> dict:
        roots = [self.resolve(folder)] if folder else self.roots
        q = query.lower().strip()
        results = []
        pattern = q if any(c in q for c in "*?[") else f"*{q}*"
        deadline = time.time() + 20
        for root in roots:
            for p in self._walk(root):
                if time.time() > deadline or len(results) >= 50:
                    break
                if by == "content":
                    if p.suffix.lower() not in TEXT_EXTS | {".pdf", ".docx"} or p.stat().st_size > 5 * 1024 * 1024:
                        continue
                    try:
                        text = extract_text(p)
                    except Exception:  # noqa: BLE001
                        continue
                    idx = text.lower().find(q)
                    if idx >= 0:
                        snippet = text[max(0, idx - 80): idx + 120].replace("\n", " ")
                        results.append({**self._info(p), "snippet": snippet})
                elif fnmatch.fnmatch(p.name.lower(), pattern):
                    results.append(self._info(p))
        return {"query": query, "by": by, "results": results, "truncated": len(results) >= 50}

    def _check_write(self):
        if not self.settings["permissions"].get("file_write", True):
            raise FileAccessError("File changes are turned off in the Click Companion settings.")

    def rename_file(self, path: str, new_name: str, **_) -> dict:
        self._check_write()
        p = self.resolve(path)
        if "/" in new_name or "\\" in new_name or new_name in ("", ".", ".."):
            raise FileAccessError("The new name can't contain folder separators.")
        dest = p.with_name(new_name)
        if dest.exists():
            raise FileAccessError(f"{new_name} already exists.")
        p.rename(dest)
        return {"status": "renamed", "from": str(p), "to": str(dest)}

    def move_file(self, path: str, destination_folder: str, **_) -> dict:
        self._check_write()
        p = self.resolve(path)
        dest_dir = self.resolve(destination_folder)
        if not dest_dir.is_dir():
            raise FileAccessError("The destination is not a folder.")
        dest = dest_dir / p.name
        if dest.exists():
            raise FileAccessError(f"{p.name} already exists there.")
        p.rename(dest)
        return {"status": "moved", "from": str(p), "to": str(dest)}
