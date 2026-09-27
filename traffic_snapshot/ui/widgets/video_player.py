"""Video player with analysis overlays.

Decoding uses OpenCV (the same FFmpeg backend as the analysis), which gives
frame-accurate positions so overlays line up exactly with the analysed
frames. Overlays are drawn with QPainter on top of the scaled frame; boxes of
frames between two analysed frames are interpolated for smooth display.
"""

from __future__ import annotations

import time
from collections import OrderedDict

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from traffic_snapshot.analysis.categories import CATEGORY_INFO, Category
from traffic_snapshot.analysis.postprocess import STATE_STATIONARY
from traffic_snapshot.analysis.results import AnalysisResults
from traffic_snapshot.core.formatting import fmt_timecode, parse_timecode
from traffic_snapshot.core.video import open_capture
from traffic_snapshot.reporting.charts import heatmap_rgba, rel_color

LAYERS: list[tuple[str, str, str]] = [
    ("vehicles", "Voertuigen", "Kaders rond gedetecteerde verkeersdeelnemers"),
    ("tracking", "Tracking", "Afgelegde route van de laatste seconden en volgnummer"),
    ("categories", "Categorieën", "Voertuigcategorie per verkeersdeelnemer"),
    ("movement", "Relatieve beweging", "Warm-koud kleur: warm = relatief meer, koud = relatief minder beweging"),
    ("stagnation", "Stilstand / stagnatie", "Markeert stilstaande voertuigen en wachtrij-indicaties"),
    ("density", "Drukte / dichtheid", "Voertuigdichtheid rond het huidige moment"),
]


def np_to_qimage(rgb: np.ndarray) -> QImage:
    h, w = rgb.shape[:2]
    fmt = QImage.Format_RGBA8888 if rgb.shape[2] == 4 else QImage.Format_RGB888
    return QImage(rgb.data, w, h, rgb.strides[0], fmt).copy()


class OverlayRenderer:
    def __init__(self, res: AnalysisResults) -> None:
        self.res = res
        self.layers: set[str] = {"vehicles", "movement", "stagnation"}
        self._density_cache: OrderedDict[int, QImage] = OrderedDict()
        self.density_window = max(10.0, res.bin_s * 2)
        self.max_gap_frames = int(res.fps * 1.0)

    # ------------------------------------------------------------------ data
    def boxes_at(self, frame: int) -> list[dict]:
        res = self.res
        a, b = res.neighbour_frames(frame)
        if a is None or frame - a > self.max_gap_frames:
            return []
        o = res.obs
        sa = res.slice_for_processed(a)
        out = []
        alpha = 0.0
        b_rows: dict[int, int] = {}
        if b is not None and b > a and b - a <= self.max_gap_frames and frame > a:
            alpha = (frame - a) / (b - a)
            sb = res.slice_for_processed(b)
            b_rows = {int(o["track"][i]): i for i in range(sb.start, sb.stop)}
        for i in range(sa.start, sa.stop):
            tid = int(o["track"][i])
            box = o["box"][i].astype(float)
            if alpha and tid in b_rows:
                box = box * (1 - alpha) + o["box"][b_rows[tid]].astype(float) * alpha
            out.append({
                "track": tid,
                "box": box,
                "cat": int(o["cat"][i]),
                "rel": float(o["rel"][i]),
                "state": int(o["state"][i]),
                "queue": bool(o["queue"][i]),
                "static": bool(o["static"][i]),
            })
        return out

    def density_image(self, t: float) -> QImage:
        key = int(t // (self.density_window / 2))
        img = self._density_cache.get(key)
        if img is None:
            center = (key + 0.5) * self.density_window / 2
            sel = self.res.select(center - self.density_window / 2, center + self.density_window / 2)
            grid = sel.heatmap("density")
            full = self.res.select().heatmap("density")
            vmax = max(float(np.percentile(full, 99)) * 2.0, 1e-3)
            rgba = heatmap_rgba("density", grid, (self.res.grid_cols * 8, self.res.grid_rows * 8), (0, vmax), 0.6)
            img = np_to_qimage(np.ascontiguousarray(rgba))
            self._density_cache[key] = img
            if len(self._density_cache) > 40:
                self._density_cache.popitem(last=False)
        return img

    # ------------------------------------------------------------------ paint
    def paint(self, p: QPainter, rect: QRectF, frame: int) -> None:
        res = self.res
        sx, sy = rect.width() / res.width, rect.height() / res.height
        t = frame / res.fps
        if "density" in self.layers:
            p.drawImage(rect, self.density_image(t))
        if not ({"vehicles", "tracking", "categories", "movement", "stagnation"} & self.layers):
            self._legend(p, rect)
            return
        items = self.boxes_at(frame)
        font = QFont(p.font())
        font.setPointSizeF(8)
        p.setFont(font)
        if "tracking" in self.layers and items:
            trails = res.track_history(np.array([i["track"] for i in items]), t, 3.0)
            for pts in trails.values():
                if len(pts) < 2:
                    continue
                path = QPainterPath(QPointF(rect.x() + pts[0, 0] * sx, rect.y() + pts[0, 1] * sy))
                for x, y in pts[1:]:
                    path.lineTo(rect.x() + x * sx, rect.y() + y * sy)
                p.setPen(QPen(QColor(255, 255, 255, 200), 2.2))
                p.drawPath(path)
                p.setPen(QPen(QColor(31, 78, 121, 230), 1.2))
                p.drawPath(path)
        for it in items:
            x1, y1, x2, y2 = it["box"]
            r = QRectF(rect.x() + x1 * sx, rect.y() + y1 * sy, (x2 - x1) * sx, (y2 - y1) * sy)
            stationary = it["state"] == STATE_STATIONARY
            color = QColor(255, 255, 255)
            if "categories" in self.layers:
                color = QColor(CATEGORY_INFO[Category(it["cat"])].color)
            if "movement" in self.layers and not it["static"]:
                color = QColor(*rel_color(0.0 if stationary else it["rel"]))
            width = 2.0
            style = Qt.SolidLine
            if it["static"]:
                color, style = QColor(200, 200, 200), Qt.DashLine
            draw_box = bool({"vehicles", "movement", "categories"} & self.layers) or (
                "stagnation" in self.layers and stationary)
            if "stagnation" in self.layers and not it["static"] and (stationary or it["queue"]):
                color = QColor("#EA580C") if it["queue"] else QColor("#B42318")
                width = 3.0
            if draw_box:
                p.setPen(QPen(color, width, style))
                p.setBrush(Qt.NoBrush)
                p.drawRect(r)
            tags = []
            if "categories" in self.layers:
                tags.append(CATEGORY_INFO[Category(it["cat"])].label)
            if "tracking" in self.layers:
                tags.append(f"#{it['track']}")
            if "stagnation" in self.layers and not it["static"]:
                if it["queue"]:
                    tags.append("Wachtrij-indicatie")
                elif stationary:
                    tags.append("Stilstand")
            if it["static"] and draw_box:
                tags.append("stil (hele waarneming)")
            if tags:
                text = " · ".join(tags)
                fm = p.fontMetrics()
                tw, th = fm.horizontalAdvance(text) + 8, fm.height() + 2
                tr = QRectF(r.x(), max(rect.y(), r.y() - th), tw, th)
                p.fillRect(tr, QColor(20, 28, 38, 190))
                p.setPen(QColor("white"))
                p.drawText(tr.adjusted(4, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, text)
        self._legend(p, rect)

    def _legend(self, p: QPainter, rect: QRectF) -> None:
        entries = []
        if "movement" in self.layers:
            entries.append("movement")
        if "stagnation" in self.layers:
            entries.append("stagnation")
        if "density" in self.layers:
            entries.append("density")
        if not entries:
            return
        x, y = rect.x() + 8, rect.bottom() - 8 - 22 * len(entries)
        p.fillRect(QRectF(x - 4, y - 4, 250, 22 * len(entries) + 4), QColor(20, 28, 38, 160))
        p.setPen(QColor("white"))
        for e in entries:
            if e == "movement":
                g = QLinearGradient(x, 0, x + 90, 0)
                for s in np.linspace(0, 1, 6):
                    g.setColorAt(s, QColor(*rel_color(s * 2.0)))
                p.fillRect(QRectF(x, y + 3, 90, 10), g)
                p.drawText(QPointF(x + 96, y + 12), "koud → warm (relatieve beweging)")
            elif e == "stagnation":
                p.fillRect(QRectF(x, y + 3, 12, 10), QColor("#B42318"))
                p.drawText(QPointF(x + 16, y + 12), "Stilstand")
                p.fillRect(QRectF(x + 80, y + 3, 12, 10), QColor("#EA580C"))
                p.drawText(QPointF(x + 96, y + 12), "Wachtrij-indicatie")
            else:
                g = QLinearGradient(x, 0, x + 90, 0)
                g.setColorAt(0, QColor(255, 255, 178, 60))
                g.setColorAt(1, QColor(189, 0, 38))
                p.fillRect(QRectF(x, y + 3, 90, 10), g)
                p.drawText(QPointF(x + 96, y + 12), "laag → hoog (voertuigdichtheid)")
            y += 22


class VideoCanvas(QWidget):
    clicked = Signal(float, float)  # normalised image coords

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.image: QImage | None = None
        self.frame = 0
        self.overlay: OverlayRenderer | None = None
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    def image_rect(self) -> QRectF:
        if self.image is None:
            return QRectF(self.rect())
        iw, ih = self.image.width(), self.image.height()
        s = min(self.width() / iw, self.height() / ih)
        w, h = iw * s, ih * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0F1720"))
        if self.image is None:
            p.setPen(QColor("#98A2B3"))
            p.drawText(self.rect(), Qt.AlignCenter, "Video laden…")
            return
        r = self.image_rect()
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(r, self.image)
        if self.overlay is not None:
            p.setRenderHint(QPainter.Antialiasing)
            self.overlay.paint(p, r, self.frame)
        p.end()

    def mousePressEvent(self, ev) -> None:
        r = self.image_rect()
        pos = ev.position()
        if r.contains(pos):
            self.clicked.emit((pos.x() - r.x()) / r.width(), (pos.y() - r.y()) / r.height())
        super().mousePressEvent(ev)


class VideoPlayer(QWidget):
    positionChanged = Signal(float)  # seconds

    def __init__(self, video_path: str, fps: float, frame_count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = video_path
        self.fps = fps
        self.frame_count = max(1, frame_count)
        self.cap = open_capture(video_path)
        self.next_pos = 0
        self.current = -1
        self.speed = 1.0
        self._play_origin: tuple[float, int] | None = None
        self.error: str = "" if self.cap.isOpened() else "De video kan niet worden geopend."

        self.canvas = VideoCanvas()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.timeout.connect(self._apply_pending_seek)
        self._pending_seek: int | None = None

        self.btn_start = self._tool("⏮", "Naar begin", lambda: self.seek_frame(0))
        self.btn_back10 = self._tool("−10 s", "10 seconden terug", lambda: self.step_seconds(-10))
        self.btn_prev = self._tool("◀│", "Vorig beeld", lambda: self.step_frames(-1))
        self.btn_play = QPushButton("▶  Afspelen")
        self.btn_play.setObjectName("primary")
        self.btn_play.setMinimumWidth(110)
        self.btn_play.clicked.connect(self.toggle)
        self.btn_next = self._tool("│▶", "Volgend beeld", lambda: self.step_frames(1))
        self.btn_fwd10 = self._tool("+10 s", "10 seconden vooruit", lambda: self.step_seconds(10))
        self.speed_box = QComboBox()
        for s in (0.25, 0.5, 1.0, 2.0, 4.0):
            self.speed_box.addItem(f"{s:g}×".replace(".", ","), s)
        self.speed_box.setCurrentIndex(2)
        self.speed_box.setToolTip("Afspeelsnelheid")
        self.speed_box.currentIndexChanged.connect(self._speed_changed)
        self.time_label = QLabel("00:00.0 / 00:00.0")
        self.time_label.setMinimumWidth(130)
        self.jump_edit = QLineEdit()
        self.jump_edit.setPlaceholderText("Ga naar (m:ss)")
        self.jump_edit.setMaximumWidth(110)
        self.jump_edit.returnPressed.connect(self._jump)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, self.frame_count - 1)
        self.slider.sliderMoved.connect(self._slider_moved)
        self.slider.valueChanged.connect(self._slider_value)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        for w in (self.btn_start, self.btn_back10, self.btn_prev, self.btn_play, self.btn_next, self.btn_fwd10):
            controls.addWidget(w)
        controls.addWidget(self.speed_box)
        controls.addStretch(1)
        controls.addWidget(self.time_label)
        controls.addWidget(self.jump_edit)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self.canvas, 1)
        lay.addWidget(self.slider)
        lay.addLayout(controls)
        self.setFocusPolicy(Qt.StrongFocus)
        if not self.error:
            self.seek_frame(0)

    def _tool(self, text: str, tip: str, fn) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.clicked.connect(fn)
        return b

    def set_overlay(self, overlay: OverlayRenderer | None) -> None:
        self.canvas.overlay = overlay
        self.canvas.update()

    def close_video(self) -> None:
        self.pause()
        self.cap.release()

    # ------------------------------------------------------------------ decoding
    def _read(self, idx: int) -> np.ndarray | None:
        idx = int(min(max(0, idx), self.frame_count - 1))
        if idx < self.next_pos or idx - self.next_pos > 20:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            self.next_pos = idx
        while self.next_pos < idx:
            if not self.cap.grab():
                break
            self.next_pos += 1
        ok, frame = self.cap.read()
        self.next_pos += 1
        return frame if ok else None

    def _show(self, idx: int) -> bool:
        idx = int(min(max(0, idx), self.frame_count - 1))
        frame = self._read(idx)
        if frame is None:
            return False
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        self.canvas.image = np_to_qimage(rgb)
        self.canvas.frame = idx
        self.current = idx
        self.slider.blockSignals(True)
        self.slider.setValue(idx)
        self.slider.blockSignals(False)
        self.time_label.setText(f"{fmt_timecode(idx / self.fps)} / {fmt_timecode(self.frame_count / self.fps)}")
        self.canvas.update()
        self.positionChanged.emit(idx / self.fps)
        return True

    # ------------------------------------------------------------------ public control
    @property
    def position(self) -> float:
        return max(0, self.current) / self.fps

    def is_playing(self) -> bool:
        return self.timer.isActive()

    def seek_frame(self, idx: int) -> None:
        self._show(idx)
        if self.is_playing():
            self._play_origin = (time.monotonic(), self.current)

    def seek_seconds(self, t: float) -> None:
        self.seek_frame(int(round(t * self.fps)))

    def step_frames(self, n: int) -> None:
        self.pause()
        self.seek_frame(self.current + n)

    def step_seconds(self, s: float) -> None:
        self.seek_frame(self.current + int(round(s * self.fps)))

    def play(self) -> None:
        if self.error:
            return
        if self.current >= self.frame_count - 1:
            self.seek_frame(0)
        self._play_origin = (time.monotonic(), self.current)
        self.timer.start(max(10, int(1000 / (self.fps * self.speed))))
        self.btn_play.setText("⏸  Pauzeren")

    def pause(self) -> None:
        self.timer.stop()
        self.btn_play.setText("▶  Afspelen")

    def toggle(self) -> None:
        self.pause() if self.is_playing() else self.play()

    def refresh(self) -> None:
        self.canvas.update()

    # ------------------------------------------------------------------ internals
    def _tick(self) -> None:
        if self._play_origin is None:
            return
        t0, f0 = self._play_origin
        target = f0 + int((time.monotonic() - t0) * self.fps * self.speed)
        if target >= self.frame_count - 1:
            self._show(self.frame_count - 1)
            self.pause()
            return
        if target > self.current and not self._show(target):
            self.pause()

    def _speed_changed(self) -> None:
        self.speed = float(self.speed_box.currentData())
        if self.is_playing():
            self.play()

    def _slider_moved(self, v: int) -> None:
        self._pending_seek = v
        if not self._seek_timer.isActive():
            self._seek_timer.start(40)

    def _slider_value(self, v: int) -> None:
        if not self.slider.isSliderDown():
            self.seek_frame(v)

    def _apply_pending_seek(self) -> None:
        if self._pending_seek is not None:
            self.seek_frame(self._pending_seek)
            self._pending_seek = None

    def _jump(self) -> None:
        t = parse_timecode(self.jump_edit.text())
        if t is None or t > self.frame_count / self.fps:
            self.jump_edit.setStyleSheet("border-color: #B42318;")
            return
        self.jump_edit.setStyleSheet("")
        self.jump_edit.clear()
        self.seek_seconds(t)

    def keyPressEvent(self, ev) -> None:
        k = ev.key()
        if k == Qt.Key_Space:
            self.toggle()
        elif k == Qt.Key_Left:
            self.step_frames(-1) if ev.modifiers() & Qt.ShiftModifier else self.step_seconds(-1)
        elif k == Qt.Key_Right:
            self.step_frames(1) if ev.modifiers() & Qt.ShiftModifier else self.step_seconds(1)
        else:
            super().keyPressEvent(ev)
