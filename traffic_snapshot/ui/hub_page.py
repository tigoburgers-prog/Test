"""Projecthub: overview of all local projects."""

from __future__ import annotations

from PySide6.QtCore import QSortFilterProxyModel, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from traffic_snapshot.core.formatting import fmt_datetime, fmt_duration
from traffic_snapshot.core.models import ProjectStatus
from traffic_snapshot.core.project_store import ProjectHandle, ProjectStore
from traffic_snapshot.ui.theme import STATUS_COLORS
from traffic_snapshot.ui.widgets.common import Card, label

COLUMNS = ["Projectnaam", "Aangemaakt", "Videoduur", "Resolutie", "Status"]


class HubPage(QWidget):
    newProjectRequested = Signal()
    openRequested = Signal(object)  # ProjectHandle

    def __init__(self, store: ProjectStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self.store = store
        self.handles: list[ProjectHandle] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(14)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(label("Projecten", "pageTitle"))
        titles.addWidget(label("Elk project bevat één verkeersvideo en de bijbehorende tijdelijke analyse.", "muted"))
        head.addLayout(titles, 1)
        self.btn_new = QPushButton("+  Nieuw project")
        self.btn_new.setObjectName("primary")
        self.btn_new.setMinimumHeight(36)
        self.btn_new.clicked.connect(self.newProjectRequested)
        head.addWidget(self.btn_new, 0, Qt.AlignBottom)
        root.addLayout(head)

        card = Card()
        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Zoeken op projectnaam…")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(320)
        tools.addWidget(self.search)
        tools.addStretch(1)
        self.btn_open = QPushButton("Openen")
        self.btn_open.setObjectName("primary")
        self.btn_folder = QPushButton("Projectmap openen")
        self.btn_delete = QPushButton("Verwijderen")
        self.btn_delete.setObjectName("danger")
        for b in (self.btn_open, self.btn_folder, self.btn_delete):
            tools.addWidget(b)
        card.lay.addLayout(tools)

        self.model = QStandardItemModel(0, len(COLUMNS), self)  # parented: Qt owns its lifetime
        self.model.setHorizontalHeaderLabels(COLUMNS)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(0)
        self.proxy.setSortRole(Qt.UserRole + 1)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, len(COLUMNS)):
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setDefaultSectionSize(36)
        card.lay.addWidget(self.table, 1)
        self.empty = label("Nog geen projecten. Klik op ‘Nieuw project’ om een verkeersvideo te importeren.", "muted")
        self.empty.setAlignment(Qt.AlignCenter)
        card.lay.addWidget(self.empty)
        self.broken_label = label("", "banner", wrap=True)
        self.broken_label.hide()
        card.lay.addWidget(self.broken_label)
        root.addWidget(card, 1)

        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.table.doubleClicked.connect(lambda _i: self._open())
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._update_buttons())
        self.btn_open.clicked.connect(self._open)
        self.btn_folder.clicked.connect(self._folder)
        self.btn_delete.clicked.connect(self._delete)
        self._update_buttons()

    # ------------------------------------------------------------------ data
    def refresh(self) -> None:
        self.handles, broken = self.store.list_projects()
        self.model.setRowCount(0)
        for i, h in enumerate(self.handles):
            p = h.project
            cells = [
                (p.name, p.name.lower()),
                (fmt_datetime(p.created_at), p.created_at),
                (fmt_duration(p.video.duration_s), p.video.duration_s),
                (p.video.resolution_label, p.video.width * p.video.height),
                (self._status_text(h), p.status.value),
            ]
            items = []
            for text, key in cells:
                it = QStandardItem(text)
                it.setData(key, Qt.UserRole + 1)
                it.setData(i, Qt.UserRole + 2)
                items.append(it)
            items[4].setForeground(QColor(STATUS_COLORS.get(p.status.value, "#1F2933")))
            self.model.appendRow(items)
        self.proxy.sort(1, Qt.DescendingOrder)
        self.empty.setVisible(not self.handles)
        self.table.setVisible(bool(self.handles))
        if broken:
            self.broken_label.setText(
                f"{len(broken)} projectmap(pen) kon(den) niet worden gelezen en worden niet getoond: "
                + ", ".join(b.path.name for b in broken[:5])
            )
            self.broken_label.show()
        else:
            self.broken_label.hide()
        self._update_buttons()

    @staticmethod
    def _status_text(h: ProjectHandle) -> str:
        p = h.project
        if p.status == ProjectStatus.INTERRUPTED and p.progress > 0:
            return f"{p.status.label} – {int(p.progress * 100)}%"
        return p.status.label

    def selected(self) -> ProjectHandle | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        i = self.proxy.data(rows[0], Qt.UserRole + 2)
        return self.handles[i] if i is not None and 0 <= i < len(self.handles) else None

    def _update_buttons(self) -> None:
        has = self.selected() is not None
        for b in (self.btn_open, self.btn_folder, self.btn_delete):
            b.setEnabled(has)

    # ------------------------------------------------------------------ actions
    def _open(self) -> None:
        h = self.selected()
        if h:
            self.openRequested.emit(h)

    def _folder(self) -> None:
        h = self.selected()
        if h:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(h.path)))

    def _delete(self) -> None:
        h = self.selected()
        if not h:
            return
        extra = "" if h.project.video_copied else "\n\nDe oorspronkelijke video buiten de projectmap blijft bewaard."
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Project verwijderen")
        box.setText(f"Weet u zeker dat u het project ‘{h.project.name}’ wilt verwijderen?")
        box.setInformativeText("Alle analyseresultaten, rapporten en exports in de projectmap worden definitief "
                               "verwijderd." + extra)
        yes = box.addButton("Verwijderen", QMessageBox.DestructiveRole)
        box.addButton("Annuleren", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        try:
            self.store.delete(h)
        except Exception as exc:  # noqa: BLE001
            from traffic_snapshot.core.errors import ProjectError
            from traffic_snapshot.ui.dialogs import show_error

            show_error(ProjectError("Het project kon niet worden verwijderd.",
                                    hint="Mogelijk is een bestand in de projectmap nog geopend in een ander programma.",
                                    detail=repr(exc)), self)
        self.refresh()
