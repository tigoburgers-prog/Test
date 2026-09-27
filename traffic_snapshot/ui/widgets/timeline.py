"""Timeline: activity profile, event markers, playhead and time-range selection.

* click → jump to that moment;
* click an event marker → jump to the event;
* drag → select a time period (all dashboard figures follow the selection).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from traffic_snapshot.analysis.results import EVENT_TYPES, AnalysisResults
from traffic_snapshot.core.formatting import fmt_duration

LANES = ["peak", "stagnation", "queue", "long_stop", "variability"]


class Timeline(QWidget):
    seekRequested = Signal(float)
    rangeSelected = Signal(float, float)
    eventClicked = Signal(dict)

    LEFT, RIGHT, TOP = 18, 18, 16
    PROFILE_H = 42
    LANE_H = 9

    def __init__(self, res: AnalysisResults, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.res = res
        self.duration = max(res.duration, 1e-6)
        self.position = 0.0
        self.selection: tuple[float, float] | None = None
        self._drag_start: float | None = None
        self._drag_now: float | None = None
        self._event_rects: list[tuple[QRectF, dict]] = []
        bin_s = max(1.0, res.duration / 240)
        edges, present, stationary, _ = res.select().presence_series(bin_s)
        self.edges, self.present, self.stationary = edges, present, stationary
        self.setMinimumHeight(self.TOP + self.PROFILE_H + len(LANES) * self.LANE_H + 26)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setToolTip("")

    # ------------------------------------------------------------------ geometry
    def _x(self, t: float) -> float:
        w = self.width() - self.LEFT - self.RIGHT
        return self.LEFT + w * min(max(t / self.duration, 0), 1)

    def _t(self, x: float) -> float:
        w = self.width() - self.LEFT - self.RIGHT
        return min(max((x - self.LEFT) / max(w, 1) * self.duration, 0), self.duration)

    def set_position(self, t: float) -> None:
        self.position = t
        self.update()

    def set_selection(self, sel: tuple[float, float] | None) -> None:
        self.selection = sel
        self.update()

    # ------------------------------------------------------------------ paint
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("white"))
        top = self.TOP
        base = top + self.PROFILE_H

        # time ticks
        p.setPen(QColor("#5F6B7A"))
        f = p.font()
        f.setPointSizeF(7.5)
        p.setFont(f)
        step = self._tick_step()
        t = 0.0
        while t <= self.duration + 1e-6:
            x = self._x(t)
            p.setPen(QColor("#E5E9F0"))
            p.drawLine(QPointF(x, top), QPointF(x, self.height() - 14))
            p.setPen(QColor("#5F6B7A"))
            p.drawText(QRectF(x - 30, self.height() - 14, 60, 14), Qt.AlignCenter, fmt_duration(t))
            t += step

        # activity profile
        if len(self.present) and self.present.max() > 0:
            vmax = self.present.max()
            b = self.edges[1] - self.edges[0] if len(self.edges) > 1 else 1.0
            path = QPainterPath(QPointF(self._x(0), base))
            for e, v in zip(self.edges, self.present):
                y = base - v / vmax * (self.PROFILE_H - 4)
                path.lineTo(self._x(e), y)
                path.lineTo(self._x(e + b), y)
            path.lineTo(self._x(self.duration), base)
            path.closeSubpath()
            p.fillPath(path, QColor(31, 78, 121, 60))
            p.setPen(QPen(QColor(31, 78, 121, 180), 1))
            p.drawPath(path)
            path2 = QPainterPath(QPointF(self._x(0), base))
            for e, v in zip(self.edges, self.stationary):
                y = base - v / vmax * (self.PROFILE_H - 4)
                path2.lineTo(self._x(e), y)
                path2.lineTo(self._x(e + b), y)
            path2.lineTo(self._x(self.duration), base)
            p.fillPath(path2, QColor(180, 35, 24, 90))
        p.setPen(QColor("#5F6B7A"))
        p.drawText(QRectF(self.LEFT + 2, 0, 400, top), Qt.AlignVCenter | Qt.AlignLeft,
                   "Activiteit (blauw) en stilstand (rood) · gebeurtenissen per type")

        # events
        self._event_rects = []
        for e in self.res.events:
            lane = LANES.index(e["type"]) if e["type"] in LANES else 0
            y = base + 3 + lane * self.LANE_H
            x0, x1 = self._x(e["t_start"]), self._x(e["t_end"])
            r = QRectF(x0, y, max(4.0, x1 - x0), self.LANE_H - 2)
            p.fillRect(r, QColor(EVENT_TYPES[e["type"]][1]))
            self._event_rects.append((r, e))

        # selection
        sel = self.selection
        if self._drag_start is not None and self._drag_now is not None and abs(self._drag_now - self._drag_start) > 0:
            sel = (min(self._drag_start, self._drag_now), max(self._drag_start, self._drag_now))
        if sel:
            r = QRectF(self._x(sel[0]), top, self._x(sel[1]) - self._x(sel[0]), self.height() - top - 14)
            p.fillRect(r, QColor(14, 124, 134, 40))
            p.setPen(QPen(QColor("#0E7C86"), 1.5))
            p.drawLine(r.topLeft(), r.bottomLeft())
            p.drawLine(r.topRight(), r.bottomRight())

        # playhead
        x = self._x(self.position)
        p.setPen(QPen(QColor("#B42318"), 2))
        p.drawLine(QPointF(x, top - 4), QPointF(x, self.height() - 14))
        p.end()

    def _tick_step(self) -> float:
        w = max(1, self.width() - self.LEFT - self.RIGHT)
        for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600):
            if w / (self.duration / s) >= 60:
                return float(s)
        return 7200.0

    # ------------------------------------------------------------------ mouse
    def _event_at(self, pos: QPointF) -> dict | None:
        for r, e in self._event_rects:
            if r.adjusted(-2, -2, 2, 2).contains(pos):
                return e
        return None

    def mousePressEvent(self, ev) -> None:
        if ev.button() != Qt.LeftButton:
            return
        e = self._event_at(ev.position())
        if e is not None:
            self.eventClicked.emit(e)
            return
        self._drag_start = self._t(ev.position().x())
        self._drag_now = self._drag_start

    def mouseMoveEvent(self, ev) -> None:
        if self._drag_start is not None:
            self._drag_now = self._t(ev.position().x())
            self.update()
            return
        e = self._event_at(ev.position())
        if e is not None:
            QToolTip.showText(ev.globalPosition().toPoint(),
                              f"<b>{e['label']}</b><br>{fmt_duration(e['t_start'])} – {fmt_duration(e['t_end'])}"
                              f"<br>{e['description']}<br><i>Klik om naar dit moment te gaan</i>", self)
        else:
            QToolTip.hideText()

    def mouseReleaseEvent(self, ev) -> None:
        if self._drag_start is None:
            return
        a, b = self._drag_start, self._t(ev.position().x())
        self._drag_start = self._drag_now = None
        if abs(self._x(b) - self._x(a)) < 5:
            self.seekRequested.emit(a)
        else:
            lo, hi = min(a, b), max(a, b)
            if hi - lo >= 1.0:
                self.selection = (lo, hi)
                self.rangeSelected.emit(lo, hi)
        self.update()
