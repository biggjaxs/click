"""Draw Click's app icon (violet rounded square with a white cursor) as PNGs for an .iconset."""
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath

out = Path(sys.argv[1] if len(sys.argv) > 1 else "Click.iconset")
out.mkdir(parents=True, exist_ok=True)
app = QGuiApplication(sys.argv)


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    m = size * 0.1
    grad = QLinearGradient(0, 0, 0, size)
    grad.setColorAt(0, QColor("#7f71ff"))
    grad.setColorAt(1, QColor("#5a49f0"))
    p.setBrush(grad)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(m, m, size - 2 * m, size - 2 * m, size * 0.2, size * 0.2)
    s = size / 64
    o = QPointF(23 * s, 15 * s)
    path = QPainterPath(o)
    for dx, dy in ((0, 32), (8.5, 24), (14, 36), (19, 33.5), (13.5, 22), (24, 21.5)):
        path.lineTo(o + QPointF(dx * s, dy * s))
    path.closeSubpath()
    p.setBrush(QColor("white"))
    p.drawPath(path)
    p.end()
    return img


for base in (16, 32, 128, 256, 512):
    draw(base).save(str(out / f"icon_{base}x{base}.png"))
    draw(base * 2).save(str(out / f"icon_{base}x{base}@2x.png"))
print("icons written to", out)
