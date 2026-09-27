"""Dialogs: errors, methodology, about."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from traffic_snapshot import APP_NAME, __version__
from traffic_snapshot.core.errors import AppError
from traffic_snapshot.reporting.texts import DISCLAIMER, METHODOLOGY_SECTIONS


class ErrorDialog(QDialog):
    """Plain-language message first; technical details only on request."""

    def __init__(self, err: AppError, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(err.title)
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        title = QLabel(err.title)
        title.setObjectName("sectionTitle")
        msg = QLabel(err.message)
        msg.setWordWrap(True)
        lay.addWidget(title)
        lay.addWidget(msg)
        if err.hint:
            hint = QLabel(err.hint)
            hint.setWordWrap(True)
            hint.setObjectName("muted")
            lay.addWidget(hint)
        self.details = QPlainTextEdit(err.detail or "Geen aanvullende details.")
        self.details.setReadOnly(True)
        self.details.setVisible(False)
        self.details.setMinimumHeight(140)
        lay.addWidget(self.details)
        btns = QHBoxLayout()
        toggle = QPushButton("Technische details")
        toggle.setObjectName("link")
        toggle.clicked.connect(lambda: self.details.setVisible(not self.details.isVisible()))
        copy = QPushButton("Kopiëren")
        copy.setObjectName("link")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(f"{err.title}\n{err.message}\n{err.detail}"))
        ok = QPushButton("Sluiten")
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        btns.addWidget(toggle)
        btns.addWidget(copy)
        btns.addStretch(1)
        btns.addWidget(ok)
        lay.addLayout(btns)


def show_error(err: AppError, parent: QWidget | None = None) -> None:
    ErrorDialog(err, parent).exec()


def methodology_html() -> str:
    parts = [f"<p style='background:#E8F0F8;padding:8px;border-radius:6px'><b>{DISCLAIMER.replace(chr(10), '<br>')}</b></p>"]
    for title, text in METHODOLOGY_SECTIONS:
        parts.append(f"<h3 style='margin-bottom:2px'>{title}</h3><p style='margin-top:2px'>{text}</p>")
    return "".join(parts)


class MethodologyDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, extra_html: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Methodologie")
        self.resize(640, 640)
        lay = QVBoxLayout(self)
        title = QLabel("Hoe komt de analyse tot stand?")
        title.setObjectName("pageTitle")
        lay.addWidget(title)
        body = QLabel(methodology_html() + extra_html)
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        body.setAlignment(Qt.AlignTop)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.addWidget(body)
        scroll.setWidget(holder)
        lay.addWidget(scroll, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.Close).setText("Sluiten")
        lay.addWidget(bb)


def about_text() -> str:
    return (
        f"<h3>{APP_NAME} {__version__}</h3>"
        "<p>Tijdelijke verkeersanalyse op basis van videobeelden. Volledig lokaal, zonder account of "
        "internetverbinding.</p>"
        "<p>Gebruikte open-source componenten: Qt (PySide6, LGPL), OpenCV (Apache-2.0), ONNX Runtime (MIT), "
        "YOLOX-detectiemodellen (Apache-2.0), NumPy (BSD), Matplotlib (PSF-achtig).</p>"
    )
