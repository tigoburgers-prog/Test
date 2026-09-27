"""Analysestatus: live progress, stop/resume, and the 'Analyse gereed' summary."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from traffic_snapshot.analysis.pipeline import STAGES, Progress
from traffic_snapshot.core.errors import AppError
from traffic_snapshot.core.formatting import fmt_duration, fmt_number
from traffic_snapshot.core.project_store import ProjectHandle
from traffic_snapshot.ui.widgets.common import Card, label


class StepRow(QWidget):
    def __init__(self, n: int, text: str) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        self.icon = QLabel()
        self.icon.setFixedWidth(26)
        self.icon.setAlignment(Qt.AlignCenter)
        self.text = QLabel(f"{n}. {text}")
        lay.addWidget(self.icon)
        lay.addWidget(self.text, 1)
        self.set_state("pending")

    def set_state(self, state: str) -> None:
        icon, color, weight = {
            "pending": ("○", "#A0A8B3", "normal"),
            "active": ("●", "#1F4E79", "600"),
            "done": ("✓", "#067647", "normal"),
            "stopped": ("■", "#B54708", "normal"),
        }[state]
        self.icon.setText(icon)
        self.icon.setStyleSheet(f"color: {color}; font-size: 13pt;")
        self.text.setStyleSheet(f"color: {'#1F2933' if state != 'pending' else '#5F6B7A'}; font-weight: {weight};")


class AnalysisPage(QWidget):
    stopRequested = Signal()
    resumeRequested = Signal()
    dashboardRequested = Signal()
    backRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(14)
        self.title = label("Analyse", "pageTitle")
        root.addWidget(self.title)
        self.subtitle = label("De video wordt lokaal geanalyseerd. U kunt het venster blijven gebruiken.", "muted")
        root.addWidget(self.subtitle)

        body = QHBoxLayout()
        body.setSpacing(16)
        steps_card = Card(margins=18)
        steps_card.lay.addWidget(label("Voortgang", "sectionTitle"))
        self.rows: dict[str, StepRow] = {}
        for i, (key, text) in enumerate(STAGES, 1):
            r = StepRow(i, text)
            self.rows[key] = r
            steps_card.lay.addWidget(r)
        steps_card.lay.addStretch(1)
        body.addWidget(steps_card, 2)

        status = Card(margins=18)
        status.lay.addWidget(label("Status", "sectionTitle"))
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setFormat("%p%")
        self.bar.setMinimumHeight(26)
        status.lay.addWidget(self.bar)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        self.f_task = QLabel("—")
        self.f_video = QLabel("—")
        self.f_eta = QLabel("—")
        self.f_mode = QLabel("—")
        for r, (t, w) in enumerate((("Huidige taak", self.f_task), ("Verwerkte video", self.f_video),
                                    ("Resterende tijd", self.f_eta), ("Verwerking", self.f_mode))):
            grid.addWidget(label(t, "muted"), r, 0)
            w.setWordWrap(True)
            grid.addWidget(w, r, 1)
        grid.setColumnStretch(1, 1)
        status.lay.addLayout(grid)
        self.message = label("", "infoBanner", wrap=True)
        self.message.hide()
        status.lay.addWidget(self.message)
        self.summary = label("", wrap=True)
        self.summary.hide()
        status.lay.addWidget(self.summary)
        status.lay.addStretch(1)
        btns = QHBoxLayout()
        self.btn_back = QPushButton("←  Naar projecten")
        self.btn_back.clicked.connect(self.backRequested)
        self.btn_stop = QPushButton("Analyse stoppen")
        self.btn_stop.setToolTip("De voortgang wordt bewaard; u kunt de analyse later hervatten.")
        self.btn_stop.clicked.connect(self._stop)
        self.btn_resume = QPushButton("Analyse hervatten")
        self.btn_resume.setObjectName("primary")
        self.btn_resume.clicked.connect(self.resumeRequested)
        self.btn_dash = QPushButton("Naar dashboard  →")
        self.btn_dash.setObjectName("primary")
        self.btn_dash.setMinimumHeight(38)
        self.btn_dash.clicked.connect(self.dashboardRequested)
        btns.addWidget(self.btn_back)
        btns.addStretch(1)
        btns.addWidget(self.btn_stop)
        btns.addWidget(self.btn_resume)
        btns.addWidget(self.btn_dash)
        status.lay.addLayout(btns)
        body.addWidget(status, 3)
        root.addLayout(body, 1)

    # ------------------------------------------------------------------ states
    def start(self, handle: ProjectHandle) -> None:
        self.title.setText(f"Analyse · {handle.project.name}")
        self.subtitle.setText("De video wordt lokaal geanalyseerd. De voortgang wordt regelmatig opgeslagen.")
        for r in self.rows.values():
            r.set_state("pending")
        self.bar.setValue(0)
        self.f_task.setText("Voorbereiden…")
        self.f_video.setText("—")
        self.f_eta.setText("wordt bepaald…")
        self.f_mode.setText("—")
        self.message.hide()
        self.summary.hide()
        self.btn_stop.setEnabled(True)
        self.btn_stop.setText("Analyse stoppen")
        self.btn_stop.show()
        self.btn_resume.hide()
        self.btn_dash.hide()
        self.btn_back.setEnabled(False)

    def update_progress(self, p: Progress) -> None:
        self.bar.setValue(int(p.fraction * 1000))
        order = [k for k, _ in STAGES]
        active = {k for k in (set(p.active) or {p.stage}) if k in order}
        first = min((order.index(k) for k in active), default=0)
        for i, k in enumerate(order):
            self.rows[k].set_state("active" if k in active else "done" if i < first else "pending")
        self.f_task.setText(p.message)
        if p.video_total_s:
            self.f_video.setText(f"{fmt_duration(p.video_pos_s)} van {fmt_duration(p.video_total_s)}")
        if p.eta_s is not None:
            self.f_eta.setText(f"ongeveer {fmt_duration(p.eta_s)}")
        elif p.fraction >= 0.9:
            self.f_eta.setText("bijna klaar")
        if p.device:
            if p.stride:
                per = f"elk {p.stride}e beeld" if p.stride > 1 else "elk beeld"
                self.f_mode.setText(f"{p.device} · {per}")
            else:
                self.f_mode.setText(p.device)

    def finished(self, res) -> None:
        for r in self.rows.values():
            r.set_state("done")
        self.bar.setValue(1000)
        self.f_task.setText("Analyse gereed")
        self.f_eta.setText("—")
        k = res.select().kpis()
        q = res.summary["quality"]
        self.summary.setText(
            f"<b>Analyse gereed.</b><br>{fmt_number(k['movements'])} verkeersbewegingen gevonden in "
            f"{fmt_duration(res.duration)} video, {len(res.directions)} verkeersrichting(en) en "
            f"{len(res.events)} opvallende gebeurtenis(sen).<br>"
            f"<span style='color:#5F6B7A'>{fmt_number(q['processed_frames'])} beelden geanalyseerd"
            + (f", {q['refinements']}× automatisch verfijnd" if q["refinements"] else "") + ".</span>"
        )
        self.summary.setTextFormat(Qt.RichText)
        self.summary.show()
        notes = res.summary.get("provenance", {}).get("notes", [])
        if notes:
            self.message.setText("Let op: " + " ".join(notes))
            self.message.show()
        self.btn_stop.hide()
        self.btn_resume.hide()
        self.btn_dash.show()
        self.btn_back.setEnabled(True)

    def stopped(self, err: AppError | None = None) -> None:
        for r in self.rows.values():
            if r.icon.text() == "●":
                r.set_state("stopped")
        self.f_eta.setText("—")
        if err is None:
            self.message.setText("De analyse is gestopt. De voortgang tot nu toe is bewaard; u kunt later hervatten.")
        else:
            self.message.setText(f"{err.title}: {err.message} {err.hint}".strip())
        self.message.show()
        self.btn_stop.hide()
        self.btn_resume.show()
        self.btn_dash.hide()
        self.btn_back.setEnabled(True)

    def _stop(self) -> None:
        self.btn_stop.setEnabled(False)
        self.btn_stop.setText("Stoppen…")
        self.f_task.setText("Voortgang opslaan en stoppen…")
        self.stopRequested.emit()
