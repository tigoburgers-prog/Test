"""Interactive heatmap on top of the scene's background image.

Hovering highlights the analysis area under the cursor; clicking it opens
the zone detail view. In drawing mode the user drags a rectangle to define
a custom zone.
"""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from traffic_snapshot.analysis.results import AnalysisResults, Region, Selection
from traffic_snapshot.core.formatting import fmt_number
from traffic_snapshot.reporting.charts import HEATMAP_CMAPS, heatmap_range, heatmap_rgba
from traffic_snapshot.ui.widgets.video_player import np_to_qimage


class HeatmapView(QWidget):
    regionClicked = Signal(object)  # Region
    zoneDrawn = Signal(object)  # Region

    def __init__(self, res: AnalysisResults, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.res = res
        bg = cv2.cvtColor(res.background, cv2.COLOR_BGR2RGB)
        bg = (bg * 0.7 + 255 * 0.3).astype(np.uint8)
        self.bg = np_to_qimage(np.ascontiguousarray(bg))
        self.kind = "density"
        self.grid: np.ndarray | None = None
        self.vrange = (0.0, 1.0)
        self.overlay: QImage | None = None
        self.zones: list[Region] = []
        self.highlight: Region | None = None
        self.hover: Region | None = None
        self.draw_mode = False
        self._drag: tuple[QPointF, QPointF] | None = None
        self.setMouseTracking(True)
        self.setMinimumSize(280, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_data(self, sel: Selection, kind: str) -> None:
        self.kind = kind
        self.grid = sel.heatmap(kind)
        self.vrange = heatmap_range(kind, self.grid)
        rgba = heatmap_rgba(kind, self.grid, (self.bg.width(), self.bg.height()), self.vrange, 0.8)
        self.overlay = np_to_qimage(np.ascontiguousarray(rgba))
        self.highlight = sel.region
        self.update()

    def set_draw_mode(self, on: bool) -> None:
        self.draw_mode = on
        self.setCursor(Qt.CrossCursor if on else Qt.PointingHandCursor)
        self.update()

    def image_rect(self) -> QRectF:
        iw, ih = self.bg.width(), self.bg.height()
        legend_h = 28
        s = min(self.width() / iw, (self.height() - legend_h) / ih)
        w, h = iw * s, ih * s
        return QRectF((self.width() - w) / 2, 0, w, h)

    def _norm(self, pos: QPointF) -> tuple[float, float] | None:
        r = self.image_rect()
        if not r.contains(pos):
            return None
        return (pos.x() - r.x()) / r.width(), (pos.y() - r.y()) / r.height()

    def _rect_of(self, reg: Region) -> QRectF:
        r = self.image_rect()
        return QRectF(r.x() + reg.x0 * r.width(), r.y() + reg.y0 * r.height(),
                      (reg.x1 - reg.x0) * r.width(), (reg.y1 - reg.y0) * r.height())

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("white"))
        r = self.image_rect()
        p.drawImage(r, self.bg)
        if self.overlay is not None:
            p.drawImage(r, self.overlay)
        f = p.font()
        f.setPointSizeF(8)
        p.setFont(f)
        for z in self.zones:
            zr = self._rect_of(z)
            p.setPen(QPen(QColor("#0E7C86"), 2, Qt.DashLine))
            p.drawRect(zr)
            p.fillRect(QRectF(zr.x(), zr.y(), p.fontMetrics().horizontalAdvance(z.name) + 8, 16), QColor("#0E7C86"))
            p.setPen(QColor("white"))
            p.drawText(QRectF(zr.x() + 4, zr.y(), 200, 16), Qt.AlignVCenter, z.name)
        if self.highlight is not None:
            p.setPen(QPen(QColor("#1F4E79"), 2.5))
            p.drawRect(self._rect_of(self.highlight))
        if self.hover is not None and not self.draw_mode:
            p.fillRect(self._rect_of(self.hover), QColor(255, 255, 255, 60))
            p.setPen(QPen(QColor(31, 78, 121), 1.5))
            p.drawRect(self._rect_of(self.hover))
        if self._drag is not None:
            a, b = self._drag
            p.setPen(QPen(QColor("#0E7C86"), 2))
            p.fillRect(QRectF(a, b).normalized(), QColor(14, 124, 134, 50))
            p.drawRect(QRectF(a, b).normalized())
        self._legend(p, r)
        p.end()

    def _legend(self, p: QPainter, r: QRectF) -> None:
        from matplotlib import colormaps

        y = r.bottom() + 8
        x = r.x()
        cmap = colormaps[HEATMAP_CMAPS[self.kind]]
        g = QLinearGradient(x, 0, x + 140, 0)
        for s in np.linspace(0, 1, 8):
            c = cmap(s)
            g.setColorAt(s, QColor.fromRgbF(c[0], c[1], c[2]))
        p.fillRect(QRectF(x, y, 140, 10), g)
        p.setPen(QColor("#5F6B7A"))
        lo, hi = self.vrange
        if self.kind == "movement":
            text = "koud (relatief minder beweging)  →  warm (relatief meer)"
        elif self.kind == "variability":
            text = f"gelijkmatig → sterk wisselend (0 – {fmt_number(hi, 1)})"
        elif self.kind == "stagnation":
            text = f"weinig → veel stilstand (0 – {fmt_number(hi, 2)} voertuigen gem.)"
        else:
            text = f"rustig → druk (0 – {fmt_number(hi, 2)} voertuigen gem.)"
        p.drawText(QRectF(x + 148, y - 3, r.width() - 150, 16), Qt.AlignVCenter | Qt.AlignLeft, text)

    # ------------------------------------------------------------------ mouse
    def mouseMoveEvent(self, ev) -> None:
        if self._drag is not None:
            self._drag = (self._drag[0], ev.position())
            self.update()
            return
        n = self._norm(ev.position())
        self.hover = self.res.coarse_region_at(*n) if n else None
        if n and self.grid is not None:
            gy = min(self.grid.shape[0] - 1, int(n[1] * self.grid.shape[0]))
            gx = min(self.grid.shape[1] - 1, int(n[0] * self.grid.shape[1]))
            v = self.grid[gy, gx]
            val = "geen gegevens" if not np.isfinite(v) else fmt_number(float(v), 2)
            self.setToolTip(f"{self.hover.name}: {val}\nKlik voor details van dit gebied"
                            if not self.draw_mode else "Sleep om een zone te tekenen")
        self.update()

    def leaveEvent(self, _ev) -> None:
        self.hover = None
        self.update()

    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.LeftButton:
            return
        if self.draw_mode:
            if self._norm(ev.position()):
                self._drag = (ev.position(), ev.position())
            return
        n = self._norm(ev.position())
        if n:
            for z in self.zones:
                if z.x0 <= n[0] < z.x1 and z.y0 <= n[1] < z.y1:
                    self.regionClicked.emit(z)
                    return
            self.regionClicked.emit(self.res.coarse_region_at(*n))

    def mouseReleaseEvent(self, ev) -> None:
        if self._drag is None:
            return
        a, b = self._drag
        self._drag = None
        r = self.image_rect()
        x0 = min(max((min(a.x(), b.x()) - r.x()) / r.width(), 0), 1)
        x1 = min(max((max(a.x(), b.x()) - r.x()) / r.width(), 0), 1)
        y0 = min(max((min(a.y(), b.y()) - r.y()) / r.height(), 0), 1)
        y1 = min(max((max(a.y(), b.y()) - r.y()) / r.height(), 0), 1)
        self.update()
        if (x1 - x0) > 0.02 and (y1 - y0) > 0.02:
            self.zoneDrawn.emit(Region(x0, y0, x1, y1))
