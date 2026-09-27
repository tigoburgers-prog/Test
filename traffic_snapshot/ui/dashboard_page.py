"""Interactive dashboard: video + overlays, key figures, timeline and analysis panel.

All views read from the in-memory :class:`AnalysisResults`; changing the time
period, zone or visual layer never triggers a new analysis.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from traffic_snapshot.analysis.results import EVENT_TYPES, HEATMAP_KINDS, AnalysisResults, Region, Selection
from traffic_snapshot.core.formatting import fmt_duration, fmt_number
from traffic_snapshot.core.models import Zone
from traffic_snapshot.core.project_store import ProjectHandle, ProjectStore
from traffic_snapshot.reporting import charts as C
from traffic_snapshot.reporting.export import (
    context_line,
    export_events_csv,
    export_timeseries_csv,
    export_tracks_csv,
    save_figure,
)
from traffic_snapshot.reporting.texts import DISCLAIMER_ONE_LINE
from traffic_snapshot.ui.widgets.common import Card, ChartView, KpiCard, label
from traffic_snapshot.ui.widgets.heatmap_view import HeatmapView
from traffic_snapshot.ui.widgets.timeline import Timeline
from traffic_snapshot.ui.widgets.video_player import LAYERS, OverlayRenderer, VideoPlayer


class DashboardPage(QWidget):
    detailsRequested = Signal()
    zoneRequested = Signal(object)  # Region
    reportRequested = Signal()
    methodologyRequested = Signal()
    backRequested = Signal()

    def __init__(self, store: ProjectStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self.store = store
        self.handle: ProjectHandle | None = None
        self.res: AnalysisResults | None = None
        self.player: VideoPlayer | None = None
        self.overlay: OverlayRenderer | None = None
        self.timeline: Timeline | None = None
        self.t_range: tuple[float, float] | None = None
        self.region: Region | None = None
        self._dirty_tabs: set[int] = set()

        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(20, 14, 20, 10)
        self.root.setSpacing(10)

    # ------------------------------------------------------------------ build
    def load(self, handle: ProjectHandle, res: AnalysisResults) -> None:
        self.unload()
        self.handle, self.res = handle, res
        self.t_range, self.region = None, None
        self._build()
        self.refresh_all()

    def unload(self) -> None:
        if self.player is not None:
            self.player.close_video()
        while self.root.count():
            item = self.root.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.player = None
        self.timeline = None

    def _build(self) -> None:
        res, p = self.res, self.handle.project

        # header
        head = QHBoxLayout()
        tbox = QVBoxLayout()
        tbox.setSpacing(0)
        tbox.addWidget(label(p.name, "pageTitle"))
        tbox.addWidget(label(
            f"Momentopname · {fmt_duration(res.duration)} video · {p.video.resolution_label} · "
            f"geanalyseerd {p.analysis.finished_at[:16].replace('T', ' ') if p.analysis else ''}", "muted"))
        head.addLayout(tbox, 1)
        b_method = QPushButton("Methodologie")
        b_method.clicked.connect(self.methodologyRequested)
        b_details = QPushButton("Analysedetails")
        b_details.clicked.connect(self.detailsRequested)
        b_export = QToolButton()
        b_export.setText("Exporteren  ▾")
        b_export.setPopupMode(QToolButton.InstantPopup)
        b_export.setMinimumHeight(30)
        menu = QMenu(b_export)
        menu.addAction("Huidige heatmap als afbeelding…", self.export_heatmap)
        menu.addAction("Alle grafieken als afbeeldingen…", self.export_all_charts)
        menu.addAction("Videobeeld met lagen als afbeelding…", self.export_frame)
        menu.addAction("Dashboard als afbeelding…", self.export_dashboard)
        menu.addSeparator()
        menu.addAction("Gegevens als CSV (Excel)…", self.export_csv)
        menu.addSeparator()
        menu.addAction("Exportmap openen", lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self.handle.exports_dir))))
        b_export.setMenu(menu)
        b_report = QPushButton("Rapport maken")
        b_report.setObjectName("primary")
        b_report.clicked.connect(self.reportRequested)
        for b in (b_method, b_details, b_export, b_report):
            head.addWidget(b, 0, Qt.AlignBottom)
        self.root.addLayout(head)

        # selection bar
        selbar = QHBoxLayout()
        selbar.setSpacing(8)
        self.sel_label = label("", "infoBanner")
        selbar.addWidget(self.sel_label, 1)
        self.btn_clear_time = QPushButton("Hele video")
        self.btn_clear_time.setToolTip("Tijdselectie opheffen")
        self.btn_clear_time.clicked.connect(self.clear_time)
        selbar.addWidget(self.btn_clear_time)
        selbar.addWidget(label("Zone:", "muted"))
        self.zone_box = QComboBox()
        self.zone_box.setMinimumWidth(170)
        self.zone_box.activated.connect(self._zone_chosen)
        selbar.addWidget(self.zone_box)
        self.btn_draw = QToolButton()
        self.btn_draw.setText("✎ Zone tekenen")
        self.btn_draw.setCheckable(True)
        self.btn_draw.setToolTip("Sleep in de heatmap een rechthoek om een eigen analysezone te maken")
        self.btn_draw.toggled.connect(self._toggle_draw)
        selbar.addWidget(self.btn_draw)
        self.btn_zone_detail = QPushButton("Zonedetails")
        self.btn_zone_detail.clicked.connect(lambda: self.region and self.zoneRequested.emit(self.region))
        selbar.addWidget(self.btn_zone_detail)
        self.root.addLayout(selbar)

        # KPI row
        kpis = QHBoxLayout()
        kpis.setSpacing(10)
        self.kpi_cards = [KpiCard("") for _ in range(6)]
        for c in self.kpi_cards:
            kpis.addWidget(c)
        self.root.addLayout(kpis)

        # main split
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        left = Card(margins=10)
        self.player = VideoPlayer(str(self.handle.video_file), res.fps, self.handle.project.video.frame_count)
        self.overlay = OverlayRenderer(res)
        self.player.set_overlay(self.overlay)
        if self.player.error:
            left.lay.addWidget(label(
                "De video van dit project kan niet worden geopend. De analyseresultaten blijven beschikbaar.",
                "banner", wrap=True))
        left.lay.addWidget(self.player, 1)
        layer_row = QHBoxLayout()
        layer_row.setSpacing(12)
        layer_row.addWidget(label("Lagen:", "muted"))
        self.layer_boxes: dict[str, QCheckBox] = {}
        for key, text, tip in LAYERS:
            cb = QCheckBox(text)
            cb.setToolTip(tip)
            cb.setChecked(key in self.overlay.layers)
            cb.toggled.connect(lambda on, k=key: self._layer(k, on))
            self.layer_boxes[key] = cb
            layer_row.addWidget(cb)
        layer_row.addStretch(1)
        left.lay.addLayout(layer_row)
        self.timeline = Timeline(res)
        self.timeline.seekRequested.connect(self._seek)
        self.timeline.rangeSelected.connect(self.set_time_range)
        self.timeline.eventClicked.connect(self._event_clicked)
        left.lay.addWidget(self.timeline)
        left.lay.addWidget(label("Klik op de tijdlijn om naar een moment te gaan · sleep om een periode te selecteren "
                                 "· klik op een gekleurd blok voor een gebeurtenis", "kpiHint"))
        self.player.positionChanged.connect(self.timeline.set_position)
        split.addWidget(left)

        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self._tab_changed)
        # heatmap tab
        hm = QWidget()
        hl = QVBoxLayout(hm)
        top = QHBoxLayout()
        self.hm_kind = QComboBox()
        for k, (title, _d) in HEATMAP_KINDS.items():
            self.hm_kind.addItem(title, k)
        self.hm_kind.currentIndexChanged.connect(lambda _i: self._refresh_heatmap())
        top.addWidget(label("Kaart:", "muted"))
        top.addWidget(self.hm_kind, 1)
        hl.addLayout(top)
        self.hm_desc = label("", "muted", wrap=True)
        hl.addWidget(self.hm_desc)
        self.heatmap = HeatmapView(res)
        self.heatmap.regionClicked.connect(self.zoneRequested)
        self.heatmap.zoneDrawn.connect(self._zone_drawn)
        hl.addWidget(self.heatmap, 1)
        hl.addWidget(label("Klik op een gebied voor een detailweergave.", "kpiHint"))
        self.tabs.addTab(hm, "Heatmap")
        # vehicles tab
        self.ch_cat = ChartView(w=5, h=2.6)
        self.ch_dir = ChartView(w=5, h=2.6)
        self.ch_int = ChartView(w=5, h=2.6)
        self.tabs.addTab(self._scroll(self.ch_cat, self.ch_dir, self.ch_int), "Voertuigen")
        # movement tab
        self.mv_text = label("", "infoBanner", wrap=True)
        self.ch_mvs = ChartView(w=5, h=2.6)
        self.ch_mvh = ChartView(w=5, h=2.6)
        self.tabs.addTab(self._scroll(self.mv_text, self.ch_mvs, self.ch_mvh), "Beweging")
        # stagnation tab
        self.st_text = label("", "infoBanner", wrap=True)
        self.ch_pres = ChartView(w=5, h=2.6)
        self.ch_std = ChartView(w=5, h=2.6)
        self.tabs.addTab(self._scroll(self.st_text, self.ch_pres, self.ch_std), "Stilstand")
        # events tab
        ev = QWidget()
        el = QVBoxLayout(ev)
        el.addWidget(label("Klik op een gebeurtenis om naar dat moment in de video te gaan.", "muted"))
        self.ev_list = QListWidget()
        self.ev_list.itemClicked.connect(lambda it: self._event_clicked(it.data(Qt.UserRole)))
        el.addWidget(self.ev_list, 1)
        self.tabs.addTab(ev, "Gebeurtenissen")
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([900, 560])
        self.root.addWidget(split, 1)
        disc = label(DISCLAIMER_ONE_LINE, "disclaimer", wrap=True)
        self.root.addWidget(disc)
        self._reload_zones()

    @staticmethod
    def _scroll(*widgets: QWidget) -> QScrollArea:
        holder = QWidget()
        lay = QVBoxLayout(holder)
        for w in widgets:
            lay.addWidget(w)
        lay.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(holder)
        return sc

    # ------------------------------------------------------------------ selection
    def selection(self) -> Selection:
        t0, t1 = self.t_range if self.t_range else (None, None)
        return self.res.select(t0, t1, self.region)

    def set_time_range(self, t0: float, t1: float) -> None:
        self.t_range = (t0, t1)
        self.timeline.set_selection(self.t_range)
        self.refresh_all()

    def clear_time(self) -> None:
        self.t_range = None
        self.timeline.set_selection(None)
        self.refresh_all()

    def set_region(self, region: Region | None) -> None:
        self.region = region
        self._sync_zone_box()
        self.refresh_all()

    def _zones(self) -> list[Region]:
        return [Region(z.x0, z.y0, z.x1, z.y1, z.name) for z in self.handle.project.zones]

    def _reload_zones(self) -> None:
        self.heatmap.zones = self._zones()
        self._sync_zone_box()
        self.heatmap.update()

    def _sync_zone_box(self) -> None:
        self.zone_box.blockSignals(True)
        self.zone_box.clear()
        self.zone_box.addItem("Hele beeld", None)
        for r in self._zones():
            self.zone_box.addItem(r.name, r)
        if self.region is not None:
            idx = next((i for i in range(1, self.zone_box.count()) if self.zone_box.itemData(i) == self.region), -1)
            if idx < 0:
                self.zone_box.addItem(self.region.name or "Geselecteerd gebied", self.region)
                idx = self.zone_box.count() - 1
            self.zone_box.setCurrentIndex(idx)
        self.zone_box.addItem("Zones beheren…", "manage")
        self.zone_box.blockSignals(False)
        self.btn_zone_detail.setEnabled(self.region is not None)

    def _zone_chosen(self, i: int) -> None:
        data = self.zone_box.itemData(i)
        if data == "manage":
            self._manage_zones()
            self._sync_zone_box()
            return
        self.set_region(data)

    def _toggle_draw(self, on: bool) -> None:
        if on:
            self.tabs.setCurrentIndex(0)
        self.heatmap.set_draw_mode(on)

    def _zone_drawn(self, reg: Region) -> None:
        self.btn_draw.setChecked(False)
        n = len(self.handle.project.zones) + 1
        name, ok = QInputDialog.getText(self, "Zone opslaan", "Naam van de zone:", text=f"Zone {n}")
        if not ok:
            return
        z = Zone(uuid.uuid4().hex[:8], name.strip() or f"Zone {n}", reg.x0, reg.y0, reg.x1, reg.y1)
        self.handle.project.zones.append(z)
        self.store.save(self.handle)
        self._reload_zones()
        self.set_region(Region(z.x0, z.y0, z.x1, z.y1, z.name))

    def _manage_zones(self) -> None:
        zones = self.handle.project.zones
        if not zones:
            QMessageBox.information(self, "Zones", "Er zijn nog geen eigen zones. Gebruik ‘Zone tekenen’ en sleep "
                                                   "in de heatmap een rechthoek.")
            return
        name, ok = QInputDialog.getItem(self, "Zone verwijderen", "Kies een zone om te verwijderen:",
                                        [z.name for z in zones], 0, False)
        if ok:
            self.handle.project.zones = [z for z in zones if z.name != name]
            self.store.save(self.handle)
            if self.region is not None and self.region.name == name:
                self.region = None
                self.refresh_all()
            self._reload_zones()

    # ------------------------------------------------------------------ refresh
    def refresh_all(self) -> None:
        if self.res is None:
            return
        sel = self.selection()
        self.sel_label.setText(f"Weergave: {sel.describe()}")
        self.btn_clear_time.setEnabled(self.t_range is not None)
        for card, (title, value, hint) in zip(self.kpi_cards, C.kpi_text(sel.kpis())):
            card.set(title, value, hint)
        self._dirty_tabs = set(range(self.tabs.count()))
        self._refresh_tab(self.tabs.currentIndex())
        self._fill_events(sel)

    def _tab_changed(self, i: int) -> None:
        if i in self._dirty_tabs:
            QTimer.singleShot(0, lambda: self._refresh_tab(i))

    def _refresh_tab(self, i: int) -> None:
        if i not in self._dirty_tabs:
            return
        self._dirty_tabs.discard(i)
        sel = self.selection()
        if i == 0:
            self._refresh_heatmap()
        elif i == 1:
            self.ch_cat.refresh(lambda f: C.fig_categories(sel, f))
            self.ch_dir.refresh(lambda f: C.fig_directions(self.res, sel, f))
            self.ch_int.refresh(lambda f: C.fig_intensity(sel, f))
        elif i == 2:
            k = sel.kpis()
            med = k["median_rel"]
            self.mv_text.setText(
                f"Bewegingspatroon: <b>{k['uniformity_label']}</b>. "
                + (f"Mediaan relatieve beweging in deze selectie: {fmt_number(med, 2)} "
                   "(1,0 = typisch voor de hele opname)." if med == med else "")
            )
            self.ch_mvs.refresh(lambda f: C.fig_movement_series(sel, f))
            self.ch_mvh.refresh(lambda f: C.fig_movement_hist(sel, f))
        elif i == 3:
            k = sel.kpis()
            eps = sel.stationary_episodes(2.0)
            self.st_text.setText(
                f"Stilstand {k['stationary_share'] * 100:.0f}% en lage beweging {k['low_share'] * 100:.0f}% van de tijd. "
                f"{len(eps)} stilstandsmoment(en) van 2 s of langer"
                + (f"; {sum(1 for e in eps if e['queue'])} daarvan in een wachtrij-indicatie." if eps else ".")
                + (f" {k['static_vehicles']} voertuig(en) stonden de gehele waarneming stil en zijn apart gehouden."
                   if k["static_vehicles"] else "")
            )
            self.ch_pres.refresh(lambda f: C.fig_presence(sel, f))
            self.ch_std.refresh(lambda f: C.fig_stationary_durations(sel, f))

    def _refresh_heatmap(self) -> None:
        kind = self.hm_kind.currentData()
        self.hm_desc.setText(HEATMAP_KINDS[kind][1])
        self.heatmap.set_data(self.selection(), kind)

    def _fill_events(self, sel: Selection) -> None:
        self.ev_list.clear()
        events = sel.events()
        if not events:
            it = QListWidgetItem("Geen bijzondere gebeurtenissen in deze selectie.")
            it.setFlags(Qt.NoItemFlags)
            self.ev_list.addItem(it)
            return
        for e in events:
            it = QListWidgetItem(f"{fmt_duration(e['t_start'])}–{fmt_duration(e['t_end'])}   {e['label']}\n"
                                 f"{e['description']}")
            it.setData(Qt.UserRole, e)
            it.setForeground(QColor(EVENT_TYPES[e["type"]][1]))
            self.ev_list.addItem(it)

    # ------------------------------------------------------------------ player interaction
    def _layer(self, key: str, on: bool) -> None:
        (self.overlay.layers.add if on else self.overlay.layers.discard)(key)
        self.player.refresh()

    def _seek(self, t: float) -> None:
        self.player.seek_seconds(t)

    def _event_clicked(self, e: dict | None) -> None:
        if not e:
            return
        self.player.pause()
        self.player.seek_seconds(max(0.0, e["t_start"] - 0.5))
        if e["type"] in ("stagnation", "queue", "long_stop") and not self.layer_boxes["stagnation"].isChecked():
            self.layer_boxes["stagnation"].setChecked(True)

    def pause(self) -> None:
        if self.player is not None:
            self.player.pause()

    # ------------------------------------------------------------------ export
    def _ask_path(self, title: str, default_name: str, filters: str) -> Path | None:
        path, _ = QFileDialog.getSaveFileName(self, title, str(self.handle.exports_dir / default_name), filters)
        return Path(path) if path else None

    def _done(self, path: Path) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Export gereed")
        box.setText(f"Opgeslagen als:\n{path}")
        open_btn = box.addButton("Map openen", QMessageBox.ActionRole)
        box.addButton("Sluiten", QMessageBox.AcceptRole)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent if path.is_file() else path)))

    def export_heatmap(self) -> None:
        kind = self.hm_kind.currentData()
        path = self._ask_path("Heatmap exporteren", f"heatmap_{kind}.png", "PNG (*.png);;JPEG (*.jpg)")
        if path:
            fig = C.fig_heatmap(self.res, self.selection(), kind, C.new_figure(9, 6))
            self._done(save_figure(fig, path, self.handle.project.name))

    def export_all_charts(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Map voor grafieken kiezen", str(self.handle.exports_dir))
        if not folder:
            return
        out = Path(folder)
        sel = self.selection()
        name = self.handle.project.name
        figs = {
            "heatmap_voertuigdichtheid": C.fig_heatmap(self.res, sel, "density", C.new_figure(9, 6)),
            "heatmap_relatieve_beweging": C.fig_heatmap(self.res, sel, "movement", C.new_figure(9, 6)),
            "heatmap_bewegingsspreiding": C.fig_heatmap(self.res, sel, "variability", C.new_figure(9, 6)),
            "heatmap_stilstand": C.fig_heatmap(self.res, sel, "stagnation", C.new_figure(9, 6)),
            "verkeersstromen": C.fig_flow_map(self.res, sel, C.new_figure(9, 6)),
            "categorieen": C.fig_categories(sel, C.new_figure(8, 4.5)),
            "richtingen": C.fig_directions(self.res, sel, C.new_figure(8, 4.5)),
            "verkeersintensiteit": C.fig_intensity(sel, C.new_figure(10, 4.5)),
            "dichtheid_en_stilstand": C.fig_presence(sel, C.new_figure(10, 4.5)),
            "relatieve_beweging_tijd": C.fig_movement_series(sel, C.new_figure(10, 4.5)),
            "relatieve_beweging_verdeling": C.fig_movement_hist(sel, C.new_figure(8, 4.5)),
            "stilstandsduur": C.fig_stationary_durations(sel, C.new_figure(8, 4.5)),
        }
        for fname, fig in figs.items():
            save_figure(fig, out / f"{fname}.png", name)
        self._done(out / "heatmap_voertuigdichtheid.png")

    def _grab_with_footer(self, widget: QWidget) -> QPixmap:
        pix = widget.grab()
        out = QPixmap(pix.width(), pix.height() + 26)
        out.fill(QColor("white"))
        p = QPainter(out)
        p.drawPixmap(0, 0, pix)
        p.setPen(QColor("#5F6B7A"))
        p.drawText(8, pix.height() + 18, f"{context_line(self.handle.project.name)} · "
                                         f"Weergave: {self.selection().describe()}")
        p.end()
        return out

    def export_frame(self) -> None:
        t = self.player.position
        path = self._ask_path("Videobeeld exporteren", f"videobeeld_{int(t)}s.png", "PNG (*.png);;JPEG (*.jpg)")
        if path:
            self._grab_with_footer(self.player.canvas).save(str(path), quality=92)
            self._done(path)

    def export_dashboard(self) -> None:
        path = self._ask_path("Dashboard exporteren", "dashboard.png", "PNG (*.png);;JPEG (*.jpg)")
        if path:
            self._grab_with_footer(self).save(str(path), quality=92)
            self._done(path)

    def export_csv(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Map voor CSV-bestanden kiezen", str(self.handle.exports_dir))
        if not folder:
            return
        sel = self.selection()
        out = Path(folder)
        export_tracks_csv(self.res, sel, out / "voertuigen.csv")
        export_timeseries_csv(sel, out / "tijdreeks.csv")
        export_events_csv(sel, out / "gebeurtenissen.csv")
        self._done(out / "voertuigen.csv")
