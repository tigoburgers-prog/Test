"""Main window and navigation.

Projecthub → Nieuw project → Instellingen → Analyse → Dashboard → Details/Zone → Rapport
"""

from __future__ import annotations

import logging
import uuid

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from traffic_snapshot import APP_NAME, __version__
from traffic_snapshot.analysis.results import AnalysisResults, Region
from traffic_snapshot.core.errors import AppError, ProjectError
from traffic_snapshot.core.models import ProjectStatus, Zone
from traffic_snapshot.core.paths import logs_dir
from traffic_snapshot.core.project_store import ProjectHandle, ProjectStore
from traffic_snapshot.ui.analysis_page import AnalysisPage
from traffic_snapshot.ui.dashboard_page import DashboardPage
from traffic_snapshot.ui.details_page import DetailsPage, ZonePage
from traffic_snapshot.ui.dialogs import MethodologyDialog, about_text, show_error
from traffic_snapshot.ui.hub_page import HubPage
from traffic_snapshot.ui.new_project_page import NewProjectPage
from traffic_snapshot.ui.report_dialog import ReportDialog
from traffic_snapshot.ui.settings_page import SettingsPage
from traffic_snapshot.ui.widgets.common import label, set_busy
from traffic_snapshot.ui.workers import Task, analysis_task

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, store: ProjectStore) -> None:
        super().__init__()
        self.store = store
        self.handle: ProjectHandle | None = None
        self.results: AnalysisResults | None = None
        self.task: Task | None = None
        self.setWindowTitle(APP_NAME)
        self.resize(1440, 900)
        self.setMinimumSize(1100, 700)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QFrame()
        header.setObjectName("headerBar")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(20, 8, 20, 8)
        self.home_btn = QPushButton(f"🚦  {APP_NAME}")
        self.home_btn.setObjectName("link")
        self.home_btn.setStyleSheet("font-size: 13pt; font-weight: 600;")
        self.home_btn.setToolTip("Naar het projectoverzicht")
        self.home_btn.clicked.connect(self.go_hub)
        hl.addWidget(self.home_btn)
        self.crumb = label("", "breadcrumb")
        hl.addWidget(self.crumb, 1)
        hl.addWidget(label("Lokaal · offline", "muted"))
        root.addWidget(header)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.hub = HubPage(store)
        self.new_page = NewProjectPage(store)
        self.settings_page = SettingsPage(store)
        self.analysis_page = AnalysisPage()
        self.dashboard = DashboardPage(store)
        self.details = DetailsPage()
        self.zone_page = ZonePage()
        for p in (self.hub, self.new_page, self.settings_page, self.analysis_page, self.dashboard,
                  self.details, self.zone_page):
            self.stack.addWidget(p)

        self.hub.newProjectRequested.connect(self.go_new)
        self.hub.openRequested.connect(self.open_project)
        self.new_page.cancelled.connect(self.go_hub)
        self.new_page.projectCreated.connect(self._project_created)
        self.settings_page.backRequested.connect(self.go_hub)
        self.settings_page.startRequested.connect(self.start_analysis)
        self.settings_page.openResultsRequested.connect(lambda: self.open_project(self.handle, force_dashboard=True))
        self.analysis_page.stopRequested.connect(self.stop_analysis)
        self.analysis_page.resumeRequested.connect(lambda: self.start_analysis(True))
        self.analysis_page.dashboardRequested.connect(self.go_dashboard)
        self.analysis_page.backRequested.connect(self.go_hub)
        self.dashboard.detailsRequested.connect(self.go_details)
        self.dashboard.zoneRequested.connect(self.go_zone)
        self.dashboard.reportRequested.connect(self.make_report)
        self.dashboard.methodologyRequested.connect(self.show_methodology)
        self.details.backRequested.connect(lambda: self._show(self.dashboard, "Dashboard"))
        self.details.seekRequested.connect(self._seek_from_details)
        self.zone_page.backRequested.connect(lambda: self._show(self.dashboard, "Dashboard"))
        self.zone_page.applyRequested.connect(self._apply_zone)
        self.zone_page.saveRequested.connect(self._save_zone)

        self._menus()
        self.go_hub()

    # ------------------------------------------------------------------ menus
    def _menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&Bestand")
        a = QAction("Nieuw project…", self)
        a.setShortcut(QKeySequence.New)
        a.triggered.connect(self.go_new)
        m.addAction(a)
        a = QAction("Projectoverzicht", self)
        a.setShortcut("Ctrl+H")
        a.triggered.connect(self.go_hub)
        m.addAction(a)
        a = QAction("Projectenmap openen", self)
        a.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.root))))
        m.addAction(a)
        m.addSeparator()
        a = QAction("Afsluiten", self)
        a.setShortcut(QKeySequence.Quit)
        a.triggered.connect(self.close)
        m.addAction(a)
        h = mb.addMenu("&Help")
        a = QAction("Methodologie en disclaimer", self)
        a.setShortcut("F1")
        a.triggered.connect(self.show_methodology)
        h.addAction(a)
        a = QAction("Logbestanden openen (diagnose)", self)
        a.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(logs_dir()))))
        h.addAction(a)
        a = QAction(f"Over {APP_NAME}", self)
        a.triggered.connect(lambda: QMessageBox.about(self, f"Over {APP_NAME}", about_text()))
        h.addAction(a)

    # ------------------------------------------------------------------ navigation
    def _show(self, page: QWidget, crumb: str) -> None:
        if page is not self.dashboard and self.stack.currentWidget() is self.dashboard:
            self.dashboard.pause()
        self.stack.setCurrentWidget(page)
        name = f" ›  {self.handle.project.name}" if self.handle and page is not self.hub else ""
        self.crumb.setText(f"   Projecten{name}  ›  {crumb}" if page is not self.hub else "   Projecten")

    def _analysis_running(self) -> bool:
        return self.task is not None and self.task.is_running()

    def go_hub(self) -> None:
        if self._analysis_running():
            self._show(self.analysis_page, "Analyse")
            QMessageBox.information(self, "Analyse bezig",
                                    "Er loopt nog een analyse. Stop de analyse eerst (de voortgang wordt bewaard).")
            return
        self.dashboard.unload()
        self.handle, self.results = None, None
        self.hub.refresh()
        self._show(self.hub, "")

    def go_new(self) -> None:
        if self._analysis_running():
            self.go_hub()
            return
        self.new_page.reset()
        self.handle = None
        self._show(self.new_page, "Nieuw project")

    def _project_created(self, handle: ProjectHandle) -> None:
        self.handle = handle
        self.settings_page.load(handle)
        self._show(self.settings_page, "Instellingen")

    def open_project(self, handle: ProjectHandle | None, force_dashboard: bool = False) -> None:
        if handle is None:
            return
        try:
            handle = self.store.load(handle.path)  # fresh state from disk
        except ProjectError as exc:
            show_error(exc, self)
            return
        self.handle = handle
        if handle.project.status == ProjectStatus.COMPLETED and handle.has_results():
            set_busy(self, True)
            try:
                self.results = AnalysisResults.load(handle.results_dir)
            except Exception as exc:  # noqa: BLE001
                set_busy(self, False)
                log.exception("Loading results failed")
                show_error(ProjectError("De opgeslagen analyseresultaten zijn onleesbaar.",
                                        hint="Voer de analyse opnieuw uit om het project te herstellen.",
                                        detail=repr(exc)), self)
                self.settings_page.load(handle)
                self._show(self.settings_page, "Instellingen")
                return
            set_busy(self, False)
            self.go_dashboard()
        else:
            self.settings_page.load(handle)
            self._show(self.settings_page, "Instellingen")

    def go_dashboard(self) -> None:
        if self.results is None and self.handle is not None and self.handle.has_results():
            self.results = AnalysisResults.load(self.handle.results_dir)
        if self.results is None:
            return
        set_busy(self, True)
        try:
            self.dashboard.load(self.handle, self.results)
        finally:
            set_busy(self, False)
        self._show(self.dashboard, "Dashboard")

    def go_details(self) -> None:
        self.details.load(self.results, self.dashboard.selection(), self.handle.project.name)
        self._show(self.details, "Analysedetails")

    def go_zone(self, region: Region) -> None:
        saved = any(z.name == region.name for z in self.handle.project.zones)
        self.zone_page.load(self.results, region, self.dashboard.t_range, saved)
        self._show(self.zone_page, f"Zone · {region.name}")

    def _apply_zone(self, region: Region) -> None:
        self.dashboard.set_region(region)
        self._show(self.dashboard, "Dashboard")

    def _save_zone(self, region: Region) -> None:
        name, ok = QInputDialog.getText(self, "Zone opslaan", "Naam van de zone:", text=region.name)
        if not ok:
            return
        z = Zone(uuid.uuid4().hex[:8], name.strip() or region.name, region.x0, region.y0, region.x1, region.y1)
        self.handle.project.zones.append(z)
        self.store.save(self.handle)
        self.dashboard._reload_zones()
        self._apply_zone(Region(z.x0, z.y0, z.x1, z.y1, z.name))

    def _seek_from_details(self, t: float) -> None:
        self._show(self.dashboard, "Dashboard")
        self.dashboard.player.pause()
        self.dashboard.player.seek_seconds(max(0.0, t - 0.5))

    def show_methodology(self) -> None:
        extra = ""
        if self.results is not None:
            prov = self.results.summary.get("provenance", {})
            extra = (f"<h3>Deze analyse</h3><p>Uitgevoerd met {APP_NAME} {prov.get('app_version', '')}, "
                     f"analysemethode {prov.get('method_version', '')}, detectie: "
                     f"{prov.get('detector', {}).get('description', '—')}, rekeneenheid: "
                     f"{prov.get('compute_device', '—')}.</p>")
        MethodologyDialog(self, extra).exec()

    def make_report(self) -> None:
        if self.results is not None:
            ReportDialog(self.handle, self.results, self.dashboard.selection(), self).exec()

    # ------------------------------------------------------------------ analysis
    def start_analysis(self, resume: bool) -> None:
        if self.handle is None or self._analysis_running():
            return
        if not resume:
            if self.handle.project.status == ProjectStatus.COMPLETED:
                box = QMessageBox.question(self, "Opnieuw analyseren",
                                           "De bestaande resultaten worden vervangen. Doorgaan?")
                if box != QMessageBox.Yes:
                    return
            self.store.reset_analysis(self.handle)
        self.results = None
        self.dashboard.unload()
        self.analysis_page.start(self.handle)
        self._show(self.analysis_page, "Analyse")
        self.task = analysis_task(self.handle, self.store, resume, self)
        self.task.progress.connect(self.analysis_page.update_progress)
        self.task.finished.connect(self._analysis_done)
        self.task.failed.connect(self._analysis_failed)
        self.task.cancelled.connect(self._analysis_cancelled)
        self.task.start()

    def stop_analysis(self) -> None:
        if self.task is not None:
            self.task.cancel()

    def _analysis_done(self, res: AnalysisResults) -> None:
        self.task = None
        self.results = res
        self.handle = self.store.load(self.handle.path)
        self.analysis_page.finished(res)
        self._show(self.analysis_page, "Analyse gereed")

    def _analysis_failed(self, err: AppError) -> None:
        self.task = None
        self.handle = self.store.load(self.handle.path)
        self.analysis_page.stopped(err)
        show_error(err, self)

    def _analysis_cancelled(self) -> None:
        self.task = None
        self.handle = self.store.load(self.handle.path)
        self.analysis_page.stopped(None)

    # ------------------------------------------------------------------ close
    def closeEvent(self, ev) -> None:
        if self._analysis_running():
            r = QMessageBox.question(
                self, "Analyse bezig",
                "Er loopt nog een analyse. Wilt u afsluiten? De voortgang wordt bewaard en de analyse kan later "
                "worden hervat.")
            if r != QMessageBox.Yes:
                ev.ignore()
                return
            set_busy(self, True)
            self.task.cancel()
            self.task.wait(60000)
            set_busy(self, False)
        self.dashboard.unload()
        ev.accept()


def version_string() -> str:
    return f"{APP_NAME} {__version__}"
