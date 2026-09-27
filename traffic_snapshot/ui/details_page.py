"""Analysedetails and zone detail views."""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from traffic_snapshot.analysis.categories import CATEGORY_INFO, Category
from traffic_snapshot.analysis.results import AnalysisResults, Region, Selection
from traffic_snapshot.core.formatting import fmt_duration, fmt_number
from traffic_snapshot.reporting import charts as C
from traffic_snapshot.ui.widgets.common import Card, ChartView, KpiCard, label
from traffic_snapshot.ui.widgets.video_player import np_to_qimage


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().hide()
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setAlternatingRowColors(True)
    t.setShowGrid(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    t.setSortingEnabled(True)
    return t


class SortableItem(QTableWidgetItem):
    def __lt__(self, other) -> bool:
        a, b = self.data(Qt.UserRole), other.data(Qt.UserRole)
        if a is not None and b is not None:
            return a < b
        return super().__lt__(other)


def _num_item(v: float, text: str | None = None) -> QTableWidgetItem:
    it = SortableItem(text if text is not None else fmt_number(v))
    it.setData(Qt.UserRole, v)
    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return it


def _page(*widgets: QWidget) -> QScrollArea:
    holder = QWidget()
    lay = QVBoxLayout(holder)
    lay.setSpacing(12)
    for w in widgets:
        if isinstance(w, QHBoxLayout):
            lay.addLayout(w)
        else:
            lay.addWidget(w)
    lay.addStretch(1)
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setWidget(holder)
    return sc


def _pair(a: QWidget, b: QWidget) -> QHBoxLayout:
    h = QHBoxLayout()
    h.addWidget(a, 1)
    h.addWidget(b, 1)
    return h


class DetailsPage(QWidget):
    backRequested = Signal()
    seekRequested = Signal(float)  # go to dashboard at this moment

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self.res: AnalysisResults | None = None
        self.sel: Selection | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 12)
        head = QHBoxLayout()
        back = QPushButton("←  Terug naar dashboard")
        back.clicked.connect(self.backRequested)
        head.addWidget(back)
        self.title = label("Analysedetails", "pageTitle")
        head.addSpacing(12)
        head.addWidget(self.title, 1)
        root.addLayout(head)
        self.sel_label = label("", "infoBanner")
        root.addWidget(self.sel_label)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        # Relatieve beweging
        self.mv_heat = ChartView(w=6.5, h=4.2)
        self.mv_var = ChartView(w=6.5, h=4.2)
        self.mv_series = ChartView(w=7, h=3)
        self.mv_hist = ChartView(w=6, h=3)
        self.mv_text = label("", "infoBanner", wrap=True)
        self.dir_table = _table(["Richting", "Omschrijving (in beeld)", "Verkeersbewegingen",
                                 "Gem. relatieve beweging", "Aandeel lage beweging"])
        self.dir_table.setMinimumHeight(150)
        self.tabs.addTab(_page(self.mv_text, _pair(self.mv_heat, self.mv_var), self.mv_series,
                               _pair(self.mv_hist, self.dir_table)), "Relatieve beweging")

        # Voertuigtelling
        self.cnt_table = _table(["Categorie"])
        self.cnt_table.setMinimumHeight(200)
        self.cnt_int = ChartView(w=7, h=3)
        self.cnt_cat = ChartView(w=5, h=3)
        self.cnt_dir = ChartView(w=5, h=3)
        self.cnt_flow = ChartView(w=6.5, h=4.2)
        self.tabs.addTab(_page(self.cnt_int, _pair(self.cnt_cat, self.cnt_dir),
                               _pair(self.cnt_flow, self.cnt_table)), "Voertuigtelling")

        # Stilstand
        self.st_text = label("", "infoBanner", wrap=True)
        self.st_heat = ChartView(w=6.5, h=4.2)
        self.st_pres = ChartView(w=7, h=3)
        self.st_dur = ChartView(w=6, h=3)
        self.st_table = _table(["Begin", "Duur (s)", "Categorie", "Wachtrij-indicatie", "Voertuig"])
        self.st_table.setMinimumHeight(220)
        self.st_table.cellDoubleClicked.connect(self._episode_clicked)
        self.ev_table = _table(["Begin", "Einde", "Gebeurtenis", "Omschrijving"])
        self.ev_table.setMinimumHeight(180)
        self.ev_table.cellDoubleClicked.connect(self._event_clicked)
        self.tabs.addTab(_page(self.st_text, _pair(self.st_heat, self.st_dur), self.st_pres,
                               label("Stilstandsmomenten (dubbelklik om in de video te bekijken)", "sectionTitle"),
                               self.st_table,
                               label("Stagnatie en wachtrij-indicaties", "sectionTitle"), self.ev_table),
                         "Stilstand")

        # Drukte
        self.dn_heat = ChartView(w=6.5, h=4.2)
        self.dn_pres = ChartView(w=7, h=3)
        self.tabs.addTab(_page(_pair(self.dn_heat, QWidget()), self.dn_pres), "Drukte")
        self.tabs.currentChanged.connect(lambda _i: self._refresh_current())
        self._done: set[int] = set()

    def load(self, res: AnalysisResults, sel: Selection, project_name: str) -> None:
        self.res, self.sel = res, sel
        self.title.setText(f"Analysedetails · {project_name}")
        self.sel_label.setText(f"Weergave: {sel.describe()} (wijzig de selectie in het dashboard)")
        self._done = set()
        self._refresh_current()

    def _refresh_current(self) -> None:
        i = self.tabs.currentIndex()
        if self.res is None or i in self._done:
            return
        self._done.add(i)
        res, sel = self.res, self.sel
        if i == 0:
            k = sel.kpis()
            self.mv_text.setText(
                f"Bewegingspatroon: <b>{k['uniformity_label']}</b>. De relatieve bewegingssnelheid is uitgedrukt ten "
                "opzichte van de typische beweging in deze opname (1,0). Warm = relatief meer beweging, koud = "
                "relatief minder. Gebieden die sterk afwijken van hun omgeving wijzen op lokale verstoring.")
            self.mv_heat.refresh(lambda f: C.fig_heatmap(res, sel, "movement", f))
            self.mv_var.refresh(lambda f: C.fig_heatmap(res, sel, "variability", f))
            self.mv_series.refresh(lambda f: C.fig_movement_series(sel, f))
            self.mv_hist.refresh(lambda f: C.fig_movement_hist(sel, f))
            self._fill_directions()
        elif i == 1:
            self.cnt_int.refresh(lambda f: C.fig_intensity(sel, f))
            self.cnt_cat.refresh(lambda f: C.fig_categories(sel, f))
            self.cnt_dir.refresh(lambda f: C.fig_directions(res, sel, f))
            self.cnt_flow.refresh(lambda f: C.fig_flow_map(res, sel, f))
            self._fill_counts()
        elif i == 2:
            k = sel.kpis()
            self.st_text.setText(
                f"Stilstand {fmt_number(k['stationary_share'] * 100)}% en lage beweging "
                f"{fmt_number(k['low_share'] * 100)}% van de tijd. Totale duur met wachtrij-indicatie: "
                f"{fmt_duration(k['queue_seconds'])}. Voertuigen die de gehele waarneming stilstaan "
                f"({k['static_vehicles']}) zijn niet meegenomen.")
            self.st_heat.refresh(lambda f: C.fig_heatmap(res, sel, "stagnation", f))
            self.st_pres.refresh(lambda f: C.fig_presence(sel, f))
            self.st_dur.refresh(lambda f: C.fig_stationary_durations(sel, f))
            self._fill_stationary()
        elif i == 3:
            self.dn_heat.refresh(lambda f: C.fig_heatmap(res, sel, "density", f))
            self.dn_pres.refresh(lambda f: C.fig_presence(sel, f))

    def _fill_directions(self) -> None:
        res, sel = self.res, self.sel
        T, o = res.tracks, res.obs
        t = self.dir_table
        t.setSortingEnabled(False)
        t.setRowCount(0)
        dirs = res.directions + [{"id": -1, "label": "Overig / onbepaald", "arrow": "", "description": "—"}]
        idx = sel.idx
        for d in dirs:
            rows = sel.movement_rows[T["direction"][sel.movement_rows] == d["id"]]
            if len(rows) == 0:
                continue
            m = np.isin(o["track_row"][idx], rows) & (o["state"][idx] != 2)
            rel = o["rel"][idx][m]
            low = o["state"][idx][np.isin(o["track_row"][idx], rows)] == 1
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, QTableWidgetItem(f"{d['arrow']} {d['label']}"))
            t.setItem(r, 1, QTableWidgetItem(d["description"]))
            t.setItem(r, 2, _num_item(len(rows)))
            t.setItem(r, 3, _num_item(float(np.median(rel)) if len(rel) else 0,
                                      fmt_number(float(np.median(rel)), 2) if len(rel) else "—"))
            t.setItem(r, 4, _num_item(float(low.mean()) if len(low) else 0,
                                      f"{fmt_number(low.mean() * 100)}%" if len(low) else "—"))
        t.setSortingEnabled(True)

    def _fill_counts(self) -> None:
        res, sel = self.res, self.sel
        T = res.tracks
        dirs = res.directions
        headers = ["Categorie"] + [f"{d['arrow']} {d['label']}" for d in dirs] + ["Onbepaald", "Totaal"]
        t = self.cnt_table
        t.setSortingEnabled(False)
        t.clear()
        t.setColumnCount(len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setRowCount(0)
        rows = sel.movement_rows
        totals = np.zeros(len(headers) - 1)
        for c in sorted(set(int(x) for x in T["cat"][rows])):
            rr = rows[T["cat"][rows] == c]
            vals = [int((T["direction"][rr] == d["id"]).sum()) for d in dirs] + [int((T["direction"][rr] < 0).sum())]
            vals.append(len(rr))
            totals += vals
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, QTableWidgetItem(CATEGORY_INFO[Category(c)].plural))
            for j, v in enumerate(vals, 1):
                t.setItem(r, j, _num_item(v))
        r = t.rowCount()
        t.insertRow(r)
        tot = QTableWidgetItem("Totaal")
        f = tot.font()
        f.setBold(True)
        tot.setFont(f)
        t.setItem(r, 0, tot)
        for j, v in enumerate(totals, 1):
            it = _num_item(int(v))
            it.setFont(f)
            t.setItem(r, j, it)
        ped = sel.pedestrian_count()
        if ped:
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, QTableWidgetItem(f"Voetgangers (niet in totaal): {ped}"))

    def _fill_stationary(self) -> None:
        sel = self.sel
        t = self.st_table
        t.setSortingEnabled(False)
        t.setRowCount(0)
        for ep in sel.stationary_episodes(2.0):
            r = t.rowCount()
            t.insertRow(r)
            it = SortableItem(fmt_duration(ep["t_start"]))
            it.setData(Qt.UserRole, ep["t_start"])
            t.setItem(r, 0, it)
            t.setItem(r, 1, _num_item(ep["duration"], fmt_number(ep["duration"], 1)))
            t.setItem(r, 2, QTableWidgetItem(CATEGORY_INFO[Category(ep["cat"])].label))
            t.setItem(r, 3, QTableWidgetItem("ja" if ep["queue"] else "nee"))
            t.setItem(r, 4, QTableWidgetItem(f"#{ep['track']}"))
        t.setSortingEnabled(True)
        e_t = self.ev_table
        e_t.setSortingEnabled(False)
        e_t.setRowCount(0)
        for e in sel.events():
            if e["type"] not in ("stagnation", "queue", "long_stop"):
                continue
            r = e_t.rowCount()
            e_t.insertRow(r)
            it = SortableItem(fmt_duration(e["t_start"]))
            it.setData(Qt.UserRole, e["t_start"])
            e_t.setItem(r, 0, it)
            e_t.setItem(r, 1, QTableWidgetItem(fmt_duration(e["t_end"])))
            e_t.setItem(r, 2, QTableWidgetItem(e["label"]))
            e_t.setItem(r, 3, QTableWidgetItem(e["description"]))
        e_t.setSortingEnabled(True)

    def _episode_clicked(self, row: int, _col: int) -> None:
        it = self.st_table.item(row, 0)
        if it is not None:
            self.seekRequested.emit(float(it.data(Qt.UserRole)))

    def _event_clicked(self, row: int, _col: int) -> None:
        it = self.ev_table.item(row, 0)
        if it is not None:
            self.seekRequested.emit(float(it.data(Qt.UserRole)))


class ZonePage(QWidget):
    backRequested = Signal()
    applyRequested = Signal(object)  # Region -> filter dashboard
    saveRequested = Signal(object)  # Region -> store as named zone

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self.region: Region | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 12)
        head = QHBoxLayout()
        back = QPushButton("←  Terug naar dashboard")
        back.clicked.connect(self.backRequested)
        head.addWidget(back)
        head.addSpacing(12)
        self.title = label("Zonedetails", "pageTitle")
        head.addWidget(self.title, 1)
        self.btn_apply = QPushButton("Dashboard filteren op deze zone")
        self.btn_apply.clicked.connect(lambda: self.applyRequested.emit(self.region))
        self.btn_save = QPushButton("Opslaan als zone…")
        self.btn_save.clicked.connect(lambda: self.saveRequested.emit(self.region))
        head.addWidget(self.btn_save)
        head.addWidget(self.btn_apply)
        root.addLayout(head)
        self.sel_label = label("", "infoBanner")
        root.addWidget(self.sel_label)

        holder = QWidget()
        lay = QVBoxLayout(holder)
        top = QHBoxLayout()
        img_card = Card()
        img_card.lay.addWidget(label("Ligging in beeld", "sectionTitle"))
        self.img = QLabel()
        self.img.setAlignment(Qt.AlignCenter)
        self.img.setMinimumSize(360, 220)
        img_card.lay.addWidget(self.img, 1)
        top.addWidget(img_card, 2)
        kpi_box = QWidget()
        grid = QGridLayout(kpi_box)
        grid.setSpacing(10)
        self.kpis = [KpiCard("") for _ in range(6)]
        for i, k in enumerate(self.kpis):
            grid.addWidget(k, i // 2, i % 2)
        top.addWidget(kpi_box, 3)
        lay.addLayout(top)
        self.ch_int = ChartView(w=7, h=2.8)
        self.ch_pres = ChartView(w=7, h=2.8)
        self.ch_mov = ChartView(w=7, h=2.8)
        self.ch_cat = ChartView(w=5, h=2.8)
        for w in (self.ch_int, self.ch_pres, self.ch_mov, self.ch_cat):
            lay.addWidget(w)
        lay.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(holder)
        root.addWidget(sc, 1)

    def load(self, res: AnalysisResults, region: Region, t_range: tuple[float, float] | None, is_saved: bool) -> None:
        self.region = region
        t0, t1 = t_range if t_range else (None, None)
        sel = res.select(t0, t1, region)
        self.title.setText(f"Zonedetails · {region.name or 'Geselecteerd gebied'}")
        self.sel_label.setText(f"Weergave: {sel.describe()}")
        self.btn_save.setVisible(not is_saved)
        k = sel.kpis()
        full = res.select(t0, t1).kpis()
        items = C.kpi_text(k)
        share = k["avg_present"] / max(full["avg_present"], 1e-9) * 100
        items[1] = ("Voertuigconcentratie", fmt_number(k["avg_present"], 2),
                    f"gem. tegelijk in deze zone ({fmt_number(share)}% van het totaal)")
        for card, (t, v, h) in zip(self.kpis, items):
            card.set(t, v, h)
        # image with highlighted zone
        bg = cv2.cvtColor(res.background, cv2.COLOR_BGR2RGB).copy()
        h, w = bg.shape[:2]
        dim = (bg * 0.45).astype(np.uint8)
        x0, y0, x1, y1 = int(region.x0 * w), int(region.y0 * h), int(region.x1 * w), int(region.y1 * h)
        dim[y0:y1, x0:x1] = bg[y0:y1, x0:x1]
        cv2.rectangle(dim, (x0, y0), (x1, y1), (31, 78, 121), max(2, w // 300))
        pix = QPixmap.fromImage(np_to_qimage(np.ascontiguousarray(dim)))
        self.img.setPixmap(pix.scaled(480, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.ch_int.refresh(lambda f: C.fig_intensity(sel, f))
        self.ch_pres.refresh(lambda f: C.fig_presence(sel, f))
        self.ch_mov.refresh(lambda f: C.fig_movement_series(sel, f))
        self.ch_cat.refresh(lambda f: C.fig_categories(sel, f))
