"""Analyse-instellingen: two simple choices that help optimise the analysis."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from traffic_snapshot.analysis.detection import available_models
from traffic_snapshot.analysis.strategy import build_plan
from traffic_snapshot.core.formatting import fmt_duration
from traffic_snapshot.core.models import SCENE_WIDTH_OPTIONS, SPEED_OPTIONS, ProjectStatus
from traffic_snapshot.core.project_store import ProjectHandle, ProjectStore
from traffic_snapshot.ui.widgets.common import Card, label


class OptionRow(QWidget):
    def __init__(self, options: list[tuple[int, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for value, text in options:
            b = QToolButton()
            b.setText(text)
            b.setCheckable(True)
            b.setMinimumSize(96, 40)
            b.setProperty("value", value)
            self.group.addButton(b, value)
            lay.addWidget(b)
        lay.addStretch(1)

    def value(self) -> int:
        return self.group.checkedId()

    def set_value(self, v: int) -> None:
        b = self.group.button(v)
        if b:
            b.setChecked(True)


class SettingsPage(QWidget):
    startRequested = Signal(bool)  # resume?
    backRequested = Signal()
    openResultsRequested = Signal()

    def __init__(self, store: ProjectStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self.store = store
        self.handle: ProjectHandle | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(14)
        self.title = label("Analyse-instellingen", "pageTitle")
        root.addWidget(self.title)
        self.subtitle = label("Stap 2 van 2 · Instellingen", "muted")
        root.addWidget(self.subtitle)

        self.banner = label("", "banner", wrap=True)
        self.banner.hide()
        root.addWidget(self.banner)

        card = Card(margins=20)
        card.lay.setSpacing(10)
        card.lay.addWidget(label("Verwachte voertuigsnelheid", "sectionTitle"))
        card.lay.addWidget(label(
            "Kies de snelheid die op deze weg gebruikelijk is. Dit bepaalt alleen hoe vaak beelden worden "
            "geanalyseerd; er wordt géén snelheid uit de video berekend.", "muted", wrap=True))
        self.speed = OptionRow(SPEED_OPTIONS)
        card.lay.addWidget(self.speed)
        card.lay.addSpacing(10)
        card.lay.addWidget(label("Geschatte beeldbreedte", "sectionTitle"))
        card.lay.addWidget(label(
            "Hoe breed is het zichtbare gebied ongeveer? Hiermee wordt ingeschat hoe groot voertuigen in beeld "
            "zijn, zodat de herkenning daarop kan worden afgestemd.", "muted", wrap=True))
        self.width = OptionRow(SCENE_WIDTH_OPTIONS)
        card.lay.addWidget(self.width)
        card.lay.addSpacing(10)
        card.lay.addWidget(label("Zo wordt de video geanalyseerd", "sectionTitle"))
        self.plan_label = label("", "infoBanner", wrap=True)
        card.lay.addWidget(self.plan_label)
        root.addWidget(card)
        root.addStretch(1)

        btns = QHBoxLayout()
        self.btn_back = QPushButton("←  Terug naar projecten")
        self.btn_back.clicked.connect(self.backRequested)
        self.btn_results = QPushButton("Bestaande resultaten bekijken")
        self.btn_results.clicked.connect(self.openResultsRequested)
        self.btn_restart = QPushButton("Opnieuw beginnen")
        self.btn_restart.clicked.connect(lambda: self.startRequested.emit(False))
        self.btn_start = QPushButton("Analyse starten")
        self.btn_start.setObjectName("primary")
        self.btn_start.setMinimumHeight(38)
        self.btn_start.setMinimumWidth(170)
        self.btn_start.clicked.connect(self._start)
        btns.addWidget(self.btn_back)
        btns.addStretch(1)
        btns.addWidget(self.btn_results)
        btns.addWidget(self.btn_restart)
        btns.addWidget(self.btn_start)
        root.addLayout(btns)

        self.speed.group.idClicked.connect(lambda _v: self._settings_changed())
        self.width.group.idClicked.connect(lambda _v: self._settings_changed())

    def load(self, handle: ProjectHandle) -> None:
        self.handle = handle
        p = handle.project
        self.title.setText(f"Analyse-instellingen · {p.name}")
        self.speed.set_value(p.settings.expected_speed_kmh)
        self.width.set_value(p.settings.scene_width_m)
        resumable = p.status in (ProjectStatus.INTERRUPTED, ProjectStatus.FAILED) and handle.db_path.exists()
        self._resumable = resumable
        if resumable:
            self.banner.setText(
                f"De vorige analyse is niet afgerond ({int(p.progress * 100)}% verwerkt). "
                f"{p.last_error} U kunt de analyse hervatten vanaf het laatst opgeslagen punt, of opnieuw beginnen."
            )
            self.banner.show()
            self.btn_start.setText("Analyse hervatten")
        elif p.status == ProjectStatus.COMPLETED:
            self.banner.setText("Dit project is al geanalyseerd. Een nieuwe analyse vervangt de bestaande resultaten.")
            self.banner.show()
            self.btn_start.setText("Opnieuw analyseren")
        else:
            self.banner.hide()
            self.btn_start.setText("Analyse starten")
        self.btn_restart.setVisible(resumable)
        self.btn_results.setVisible(p.status == ProjectStatus.COMPLETED and handle.has_results())
        self._update_plan()

    def _settings_changed(self) -> None:
        if not self.handle:
            return
        s = self.handle.project.settings
        changed = (s.expected_speed_kmh, s.scene_width_m) != (self.speed.value(), self.width.value())
        s.expected_speed_kmh = self.speed.value()
        s.scene_width_m = self.width.value()
        self.store.save(self.handle)
        if changed and self._resumable:
            self.banner.setText("De instellingen zijn gewijzigd; de analyse begint daarom opnieuw.")
            self.btn_start.setText("Analyse starten")
            self.btn_restart.hide()
            self._resumable = False
        self._update_plan()

    def _update_plan(self) -> None:
        if not self.handle:
            return
        p = self.handle.project
        plan = build_plan(p.video, p.settings, available_models())
        text = plan.describe_for_user()
        est = self._estimate_seconds(plan)
        if est:
            text += f"\nGeschatte duur: ongeveer {fmt_duration(est)} (afhankelijk van deze computer)."
        for n in plan.notes:
            text += f"\nLet op: {n}"
        self.plan_label.setText(text)

    def _estimate_seconds(self, plan) -> float:
        v = self.handle.project.video
        frames = v.frame_count / max(1, plan.base_stride)
        passes = 1 if plan.tiles == (1, 1) else 1 + plan.tiles[0] * plan.tiles[1]  # full frame + tiles
        per_frame = 0.012 if plan.detector == "motion" else 0.09 * passes
        per_frame *= max(1.0, (v.width * v.height) / (1920 * 1080)) ** 0.3
        return frames * per_frame + 10

    def _start(self) -> None:
        self._settings_changed()
        self.startRequested.emit(bool(getattr(self, "_resumable", False)))
