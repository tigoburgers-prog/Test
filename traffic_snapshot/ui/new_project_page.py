"""Nieuw project: import a video, check it, name the project."""

from __future__ import annotations

from pathlib import Path

import cv2
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from traffic_snapshot.core.errors import AppError
from traffic_snapshot.core.formatting import fmt_bytes, fmt_duration, fmt_number
from traffic_snapshot.core.models import VideoInfo
from traffic_snapshot.core.project_store import ProjectStore
from traffic_snapshot.core.video import SUPPORTED_EXTENSIONS, open_capture, probe_video
from traffic_snapshot.ui.dialogs import show_error
from traffic_snapshot.ui.widgets.common import Card, label, set_busy
from traffic_snapshot.ui.widgets.video_player import np_to_qimage
from traffic_snapshot.ui.workers import import_task


class DropZone(QFrame):
    fileDropped = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumHeight(150)
        self._style(False)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        icon = QLabel("🎞")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("font-size: 30pt; border: none; background: transparent;")
        t = label("Sleep een verkeersvideo hierheen")
        t.setAlignment(Qt.AlignCenter)
        t.setStyleSheet("font-size: 12pt; font-weight: 600; border: none; background: transparent;")
        s = label("of", "muted")
        s.setAlignment(Qt.AlignCenter)
        s.setStyleSheet("border: none; background: transparent; color: #5F6B7A;")
        self.btn = QPushButton("Bestand kiezen…")
        self.btn.setObjectName("primary")
        f = label("MP4 (aanbevolen), MOV, M4V, AVI of MKV", "muted")
        f.setAlignment(Qt.AlignCenter)
        f.setStyleSheet("border: none; background: transparent; color: #5F6B7A;")
        for w in (icon, t, s):
            lay.addWidget(w)
        lay.addWidget(self.btn, 0, Qt.AlignCenter)
        lay.addWidget(f)

    def _style(self, active: bool) -> None:
        self.setStyleSheet(
            "DropZone { border: 2px dashed %s; border-radius: 10px; background: %s; }"
            % (("#1F4E79", "#E8F0F8") if active else ("#B8C2CE", "#FFFFFF"))
        )

    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
            self._style(True)

    def dragLeaveEvent(self, _ev) -> None:
        self._style(False)

    def dropEvent(self, ev) -> None:
        self._style(False)
        urls = [u for u in ev.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.fileDropped.emit(urls[0].toLocalFile())


class NewProjectPage(QWidget):
    projectCreated = Signal(object)  # ProjectHandle
    cancelled = Signal()

    def __init__(self, store: ProjectStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self.store = store
        self.video_path: Path | None = None
        self.info: VideoInfo | None = None
        self.task = None

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(14)
        root.addWidget(label("Nieuw project", "pageTitle"))
        root.addWidget(label("Stap 1 van 2 · Video importeren", "muted"))

        body = QHBoxLayout()
        body.setSpacing(16)
        left = Card()
        self.drop = DropZone()
        self.drop.btn.clicked.connect(self._choose)
        self.drop.fileDropped.connect(self.load_video)
        left.lay.addWidget(self.drop)
        self.error_label = label("", "errorText", wrap=True)
        self.error_label.hide()
        left.lay.addWidget(self.error_label)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(200)
        self.preview.setStyleSheet("background: #0F1720; border-radius: 6px; color: #98A2B3;")
        self.preview.setText("Nog geen video gekozen")
        left.lay.addWidget(self.preview, 1)
        body.addWidget(left, 3)

        right = Card()
        right.lay.addWidget(label("Videogegevens", "sectionTitle"))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        self.meta = {k: QLabel("—") for k in ("file", "duration", "resolution", "fps", "size", "codec")}
        for key, text in (("file", "Bestand"), ("duration", "Videoduur"), ("resolution", "Resolutie"),
                          ("fps", "Framerate"), ("size", "Bestandsgrootte"), ("codec", "Codec")):
            self.meta[key].setWordWrap(True)
            form.addRow(label(text, "muted"), self.meta[key])
        right.lay.addLayout(form)
        right.lay.addSpacing(8)
        right.lay.addWidget(label("Projectnaam", "sectionTitle"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Bijvoorbeeld: Kruispunt Dorpsstraat – ochtendspits")
        self.name_edit.textChanged.connect(self._update_state)
        right.lay.addWidget(self.name_edit)
        self.copy_box = QCheckBox("Video naar de projectmap kopiëren (aanbevolen)")
        self.copy_box.setChecked(True)
        self.copy_box.setToolTip("Het project blijft dan ook bruikbaar als het oorspronkelijke bestand wordt "
                                 "verplaatst of verwijderd.")
        right.lay.addWidget(self.copy_box)
        self.progress = QProgressBar()
        self.progress.hide()
        right.lay.addWidget(self.progress)
        right.lay.addStretch(1)
        btns = QHBoxLayout()
        self.btn_cancel = QPushButton("Annuleren")
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_next = QPushButton("Volgende: instellingen  →")
        self.btn_next.setObjectName("primary")
        self.btn_next.clicked.connect(self._create)
        btns.addWidget(self.btn_cancel)
        btns.addStretch(1)
        btns.addWidget(self.btn_next)
        right.lay.addLayout(btns)
        body.addWidget(right, 2)
        root.addLayout(body, 1)
        self.reset()

    def reset(self) -> None:
        self.video_path, self.info = None, None
        for v in self.meta.values():
            v.setText("—")
        self.name_edit.clear()
        self.preview.setPixmap(QPixmap())
        self.preview.setText("Nog geen video gekozen")
        self.error_label.hide()
        self.progress.hide()
        self.copy_box.setChecked(True)
        self._set_enabled(True)
        self._update_state()

    def _update_state(self) -> None:
        self.btn_next.setEnabled(self.info is not None and bool(self.name_edit.text().strip()))

    def _choose(self) -> None:
        exts = " ".join(f"*{e}" for e in SUPPORTED_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "Verkeersvideo kiezen", "", f"Video's ({exts});;Alle bestanden (*)")
        if path:
            self.load_video(path)

    def load_video(self, path: str) -> None:
        self.error_label.hide()
        set_busy(self, True)
        try:
            info = probe_video(path)
        except AppError as exc:
            set_busy(self, False)
            self.info = None
            self.error_label.setText(f"{exc.title}: {exc.message} {exc.hint}".strip())
            self.error_label.show()
            self._update_state()
            return
        set_busy(self, False)
        self.video_path, self.info = Path(path), info
        self.meta["file"].setText(info.file_name)
        self.meta["duration"].setText(fmt_duration(info.duration_s, always_hours=info.duration_s >= 3600))
        self.meta["resolution"].setText(f"{info.resolution_label} pixels")
        self.meta["fps"].setText(f"{fmt_number(info.fps, 2)} beelden per seconde")
        self.meta["size"].setText(fmt_bytes(info.size_bytes))
        self.meta["codec"].setText(info.codec or "onbekend")
        if not self.name_edit.text().strip():
            self.name_edit.setText(Path(path).stem.replace("_", " "))
        self._show_preview(path, info)
        self._update_state()

    def _show_preview(self, path: str, info: VideoInfo) -> None:
        cap = open_capture(path)
        cap.set(cv2.CAP_PROP_POS_FRAMES, min(info.frame_count - 1, int(info.fps)))
        ok, frame = cap.read()
        cap.release()
        if not ok:
            return
        img = np_to_qimage(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        pix = QPixmap.fromImage(img).scaled(self.preview.width(), max(200, self.preview.height()),
                                            Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview.setPixmap(pix)

    def _set_enabled(self, on: bool) -> None:
        for w in (self.drop, self.name_edit, self.copy_box, self.btn_next):
            w.setEnabled(on)

    def _create(self) -> None:
        if not (self.info and self.video_path):
            return
        self._set_enabled(False)
        self.progress.setValue(0)
        self.progress.setVisible(self.copy_box.isChecked())
        self.task = import_task(self.store, self.name_edit.text(), self.video_path, self.info,
                                self.copy_box.isChecked(), self)
        self.task.progress.connect(lambda p: self.progress.setValue(int(p.fraction * 100)))
        self.task.finished.connect(self._created)
        self.task.failed.connect(self._failed)
        self.task.cancelled.connect(lambda: (self._set_enabled(True), self.progress.hide()))
        self.task.start()

    def _created(self, handle) -> None:
        self.task = None
        self.projectCreated.emit(handle)

    def _failed(self, err) -> None:
        self.task = None
        self._set_enabled(True)
        self.progress.hide()
        show_error(err, self)

    def _cancel(self) -> None:
        if self.task is not None and self.task.is_running():
            self.task.cancel()
            return
        self.cancelled.emit()
