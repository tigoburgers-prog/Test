"""Small reusable widgets."""

from __future__ import annotations

from collections.abc import Callable

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from traffic_snapshot.reporting.charts import new_figure


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None, margins: int = 14) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(margins, margins, margins, margins)
        self.lay.setSpacing(8)


def label(text: str = "", obj: str | None = None, wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if obj:
        lb.setObjectName(obj)
    lb.setWordWrap(wrap)
    return lb


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("color: #DCE1E8;")
    return f


class KpiCard(Card):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, margins=12)
        self.lay.setSpacing(2)
        self.title = label(title, "kpiLabel")
        self.value = label("—", "kpiValue")
        self.hint = label("", "kpiHint", wrap=True)
        self.lay.addWidget(self.title)
        self.lay.addWidget(self.value)
        self.lay.addWidget(self.hint)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumWidth(130)

    def set(self, title: str, value: str, hint: str = "") -> None:
        self.title.setText(title)
        self.value.setText(value)
        # long textual values get a smaller font so cards keep equal height
        self.value.setStyleSheet("font-size: 12pt;" if len(value) > 12 else "")
        self.hint.setText(hint)


class ChartView(QWidget):
    """A matplotlib figure inside Qt, redrawn through a render callback."""

    def __init__(self, render: Callable[[Figure], Figure] | None = None, w: float = 6, h: float = 3,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.figure = new_figure(w, h)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.canvas)
        self.render_fn = render
        self.setMinimumHeight(int(h * 60))

    def refresh(self, render: Callable[[Figure], Figure] | None = None) -> None:
        if render is not None:
            self.render_fn = render
        if self.render_fn is None:
            return
        self.render_fn(self.figure)
        self.canvas.draw_idle()


def row(*widgets: QWidget, spacing: int = 8, stretch_last: bool = False) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for x in widgets:
        if x is None:
            lay.addStretch(1)
        else:
            lay.addWidget(x)
    if stretch_last:
        lay.addStretch(1)
    return w


def set_busy(widget: QWidget, busy: bool) -> None:
    from PySide6.QtWidgets import QApplication

    if busy:
        QApplication.setOverrideCursor(Qt.WaitCursor)
    else:
        QApplication.restoreOverrideCursor()
