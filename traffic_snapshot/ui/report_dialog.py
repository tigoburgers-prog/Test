"""Rapport maken: choose sections and location, then generate the PDF locally."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from traffic_snapshot.analysis.results import AnalysisResults, Selection
from traffic_snapshot.core.errors import AppError
from traffic_snapshot.core.project_store import ProjectHandle, slugify
from traffic_snapshot.reporting.report import SECTIONS, ReportOptions, build_report
from traffic_snapshot.ui.dialogs import show_error
from traffic_snapshot.ui.widgets.common import label, set_busy


class ReportDialog(QDialog):
    def __init__(self, handle: ProjectHandle, res: AnalysisResults, sel: Selection, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.handle, self.res, self.sel = handle, res, sel
        self.setWindowTitle("Rapport maken")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.addWidget(label("Rapport maken", "pageTitle"))
        lay.addWidget(label("Het rapport wordt als PDF op deze computer opgeslagen.", "muted"))

        form = QFormLayout()
        self.title = QLineEdit(f"Verkeersanalyse – {handle.project.name}")
        self.author = QLineEdit()
        self.author.setPlaceholderText("optioneel")
        form.addRow("Titel", self.title)
        form.addRow("Opgesteld door", self.author)
        lay.addLayout(form)

        lay.addWidget(label("Onderdelen", "sectionTitle"))
        self.boxes: dict[str, QCheckBox] = {}
        grid = QHBoxLayout()
        cols = [QVBoxLayout(), QVBoxLayout()]
        for i, (key, text) in enumerate(SECTIONS):
            cb = QCheckBox(text)
            cb.setChecked(True)
            self.boxes[key] = cb
            cols[i % 2].addWidget(cb)
        for c in cols:
            grid.addLayout(c)
        lay.addLayout(grid)

        lay.addWidget(label("Periode en zone", "sectionTitle"))
        self.scope_full = QRadioButton("Volledige video, hele beeld")
        self.scope_sel = QRadioButton(f"Huidige selectie in het dashboard ({sel.describe()})")
        self.scope_full.setChecked(sel.is_full)
        self.scope_sel.setChecked(not sel.is_full)
        self.scope_sel.setEnabled(not sel.is_full)
        lay.addWidget(self.scope_full)
        lay.addWidget(self.scope_sel)

        lay.addWidget(label("Opslaan als", "sectionTitle"))
        row = QHBoxLayout()
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        self.path = QLineEdit(str(handle.reports_dir / f"Rapport_{slugify(handle.project.name)}_{stamp}.pdf"))
        browse = QPushButton("Bladeren…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        lay.addLayout(row)
        self.open_after = QCheckBox("Rapport openen na het maken")
        self.open_after.setChecked(True)
        lay.addWidget(self.open_after)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("Annuleren")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton("Rapport maken")
        self.ok.setObjectName("primary")
        self.ok.clicked.connect(self._generate)
        btns.addWidget(cancel)
        btns.addWidget(self.ok)
        lay.addLayout(btns)

    def _browse(self) -> None:
        p, _ = QFileDialog.getSaveFileName(self, "Rapport opslaan", self.path.text(), "PDF (*.pdf)")
        if p:
            self.path.setText(p if p.lower().endswith(".pdf") else p + ".pdf")

    def _generate(self) -> None:
        sections = {k for k, cb in self.boxes.items() if cb.isChecked()}
        if not sections:
            QMessageBox.information(self, "Rapport maken", "Kies ten minste één onderdeel.")
            return
        path = Path(self.path.text())
        sel = self.sel if self.scope_sel.isChecked() else self.res.select()
        set_busy(self, True)
        try:
            build_report(self.handle.project, self.res, sel, path,
                         ReportOptions(sections, self.title.text().strip(), self.author.text().strip()))
        except PermissionError as exc:
            set_busy(self, False)
            show_error(AppError("Het rapport kon niet worden opgeslagen.",
                                hint="Mogelijk is een bestand met dezelfde naam nog geopend in een PDF-lezer.",
                                detail=repr(exc)), self)
            return
        except Exception as exc:  # noqa: BLE001
            set_busy(self, False)
            show_error(AppError("Het rapport kon niet worden gemaakt.", detail=repr(exc)), self)
            return
        set_busy(self, False)
        if self.open_after.isChecked():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        self.accept()
