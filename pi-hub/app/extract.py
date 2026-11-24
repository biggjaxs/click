"""Turn uploaded files into text the assistant can read (text, code, PDF, Word, Excel, CSV)."""
from __future__ import annotations

import csv
import io
from pathlib import Path

TEXT_EXTS = {".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml", ".ini",
             ".cfg", ".html", ".css", ".xml", ".sh", ".ps1", ".bat", ".c", ".h", ".cpp", ".hpp", ".java",
             ".cs", ".go", ".rs", ".rb", ".php", ".sql", ".log", ".swift", ".kt", ".lua", ".r", ".tex"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
MAX_CHARS = 60_000


def is_image(filename: str) -> bool:
    return Path(filename).suffix.lower() in IMAGE_EXTS


def extract_text(filename: str, data: bytes) -> str | None:
    ext = Path(filename).suffix.lower()
    try:
        if ext in TEXT_EXTS:
            return data.decode("utf-8", errors="replace")[:MAX_CHARS]
        if ext == ".csv":
            rows = list(csv.reader(io.StringIO(data.decode("utf-8", errors="replace"))))
            return "\n".join(", ".join(r) for r in rows[:2000])[:MAX_CHARS]
        if ext == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            return "\n\n".join((p.extract_text() or "") for p in reader.pages[:200])[:MAX_CHARS]
        if ext == ".docx":
            import docx

            doc = docx.Document(io.BytesIO(data))
            parts = [p.text for p in doc.paragraphs]
            for table in doc.tables:
                for row in table.rows:
                    parts.append(" | ".join(c.text for c in row.cells))
            return "\n".join(parts)[:MAX_CHARS]
        if ext in (".xlsx", ".xlsm"):
            import openpyxl

            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            parts = []
            for ws in wb.worksheets[:10]:
                parts.append(f"## Sheet: {ws.title}")
                for row in ws.iter_rows(max_row=2000, values_only=True):
                    if any(v is not None for v in row):
                        parts.append(", ".join("" if v is None else str(v) for v in row))
            return "\n".join(parts)[:MAX_CHARS]
    except Exception as exc:  # noqa: BLE001 - report instead of crashing the upload
        return f"(Could not read this file: {exc})"
    return None
