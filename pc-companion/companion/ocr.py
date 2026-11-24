"""Fast text recognition (OCR) with word positions.

- macOS: Apple's Vision framework (runs on the Neural Engine; typically well under a second).
- Windows / others: RapidOCR (local ONNX model).

`recognize(img)` returns lines with boxes in IMAGE pixels: [{"text", "x", "y", "w", "h", "conf"}].
"""
from __future__ import annotations

import io
import logging
import sys
import threading

log = logging.getLogger("click.ocr")

_rapid = None
_rapid_lock = threading.Lock()


def _mac_vision(img, fast: bool) -> list[dict]:
    import Vision
    from Foundation import NSData

    buf = io.BytesIO()
    img.save(buf, "PNG")
    data = NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(1 if fast else 0)  # 1 = fast, 0 = accurate
    req.setUsesLanguageCorrection_(not fast)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(f"Vision OCR failed: {err}")
    out = []
    W, H = img.width, img.height
    for obs in req.results() or []:
        cands = obs.topCandidates_(1)
        if not cands:
            continue
        c = cands[0]
        bb = obs.boundingBox()  # normalised, origin bottom-left
        x, y = bb.origin.x * W, (1 - bb.origin.y - bb.size.height) * H
        out.append({"text": str(c.string()), "x": int(x), "y": int(y), "w": int(bb.size.width * W),
                    "h": int(bb.size.height * H), "conf": float(c.confidence())})
    return out


def _rapidocr(img) -> list[dict]:
    global _rapid
    import numpy as np

    with _rapid_lock:
        if _rapid is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ImportError:
                from rapidocr import RapidOCR
            _rapid = RapidOCR()
        result = _rapid(np.array(img))
    if hasattr(result, "txts"):  # rapidocr 2.x/3.x
        rows = list(zip(result.boxes if result.boxes is not None else [], result.txts or [], result.scores or []))
    else:                        # rapidocr_onnxruntime 1.x: (rows, elapsed)
        rows = result[0] if isinstance(result, tuple) else None
    out = []
    for box, text, conf in rows or []:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        out.append({"text": text, "x": int(min(xs)), "y": int(min(ys)), "w": int(max(xs) - min(xs)),
                    "h": int(max(ys) - min(ys)), "conf": float(conf)})
    return out


def recognize(img, fast: bool = True) -> list[dict]:
    if sys.platform == "darwin":
        try:
            return _mac_vision(img, fast)
        except Exception as exc:  # noqa: BLE001 - fall back to RapidOCR
            log.warning("Apple Vision OCR unavailable (%s); using RapidOCR", exc)
    return _rapidocr(img)


def lines_to_text(lines: list[dict], min_conf: float = 0.4) -> str:
    """Join OCR lines in reading order (top to bottom, left to right)."""
    rows = sorted((ln for ln in lines if ln["conf"] >= min_conf), key=lambda ln: (ln["y"] // 14, ln["x"]))
    out, last_row = [], None
    for ln in rows:
        row = ln["y"] // 14
        if last_row is not None and row == last_row and out:
            out[-1] += "   " + ln["text"]
        else:
            out.append(ln["text"])
        last_row = row
    return "\n".join(out)
