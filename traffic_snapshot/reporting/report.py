"""PDF report generation (fully local).

The report is composed as rich text (QTextDocument) with charts rendered by
the shared chart module, and printed to PDF with QPdfWriter. This keeps
typography, tables and pagination professional without extra dependencies.
Requires a Qt GUI application instance (the desktop app always has one).
"""

from __future__ import annotations

import html
import io
from dataclasses import dataclass, field
from pathlib import Path

from matplotlib.figure import Figure
from PySide6.QtCore import QMarginsF, QRectF, QSizeF, Qt, QUrl
from PySide6.QtGui import QColor, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter, QTextDocument

from traffic_snapshot import APP_NAME, __version__
from traffic_snapshot.analysis.categories import CATEGORY_INFO, Category
from traffic_snapshot.analysis.results import AnalysisResults, Selection
from traffic_snapshot.core.formatting import fmt_bytes, fmt_datetime, fmt_duration, fmt_number
from traffic_snapshot.core.models import Project
from traffic_snapshot.reporting import charts as C
from traffic_snapshot.reporting.texts import DISCLAIMER, METHODOLOGY_SECTIONS

SECTIONS: list[tuple[str, str]] = [
    ("overview", "Overzicht"),
    ("kpis", "Kerncijfers"),
    ("heatmaps", "Heatmaps"),
    ("vehicles", "Voertuiggegevens"),
    ("movement", "Bewegingsanalyse"),
    ("stagnation", "Stilstand en stagnatie"),
    ("methodology", "Methodologie"),
    ("disclaimer", "Disclaimer"),
]


@dataclass
class ReportOptions:
    sections: set[str] = field(default_factory=lambda: {k for k, _ in SECTIONS})
    title: str = ""
    author: str = ""


def _esc(s: str) -> str:
    return html.escape(str(s))


class _Builder:
    def __init__(self, doc: QTextDocument, width_px: int) -> None:
        self.doc = doc
        self.parts: list[str] = []
        self.images: list[tuple[str, QImage]] = []
        self.n = 0
        self.width_px = width_px

    def add(self, s: str) -> None:
        self.parts.append(s)

    def figure(self, fig: Figure, rel_width: float = 1.0) -> None:
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=170, facecolor="white")
        img = QImage.fromData(buf.getvalue(), "PNG")
        name = f"fig{self.n}.png"
        self.n += 1
        self.images.append((name, img))
        w = int(self.width_px * rel_width)
        self.add(f'<p align="center"><img src="{name}" width="{w}"></p>')


CSS = """
body { font-family: 'Segoe UI', 'DejaVu Sans', sans-serif; font-size: 9.5pt; color: #1F2933; }
h1 { font-size: 20pt; color: #1F4E79; margin-bottom: 2px; }
h2 { font-size: 13.5pt; color: #1F4E79; margin-top: 18px; margin-bottom: 4px; }
h3 { font-size: 11pt; margin-top: 10px; margin-bottom: 2px; }
p { margin-top: 3px; margin-bottom: 5px; line-height: 125%; }
.muted { color: #5F6B7A; }
.box { background-color: #E8F0F8; }
table.data { border-collapse: collapse; }
table.data td, table.data th { padding: 4px 8px; border-bottom: 1px solid #DCE1E8; }
table.data th { background-color: #F1F4F8; text-align: left; }
"""


def _kv_table(rows: list[tuple[str, str]]) -> str:
    body = "".join(f"<tr><td class='muted' width='38%'>{_esc(k)}</td><td>{_esc(v)}</td></tr>" for k, v in rows)
    return f"<table class='data' width='100%' cellspacing='0'>{body}</table>"


def _grid_table(headers: list[str], rows: list[list[str]]) -> str:
    h = "".join(f"<th>{_esc(x)}</th>" for x in headers)
    b = "".join("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table class='data' width='100%' cellspacing='0'><tr>{h}</tr>{b}</table>"


def build_report(project: Project, res: AnalysisResults, sel: Selection, path: Path,
                 options: ReportOptions | None = None) -> Path:
    opt = options or ReportOptions()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.A4))
    writer.setPageMargins(QMarginsF(16, 16, 16, 18), QPageLayout.Millimeter)
    writer.setResolution(110)
    writer.setTitle(opt.title or f"Verkeersanalyse – {project.name}")
    writer.setCreator(f"{APP_NAME} {__version__}")

    doc = QTextDocument()
    doc.setDefaultStyleSheet(CSS)
    doc.setDocumentMargin(0)
    page = writer.pageLayout().paintRectPixels(writer.resolution())
    doc.setPageSize(QSizeF(page.width(), page.height()))
    b = _Builder(doc, int(page.width() * 0.98))
    k = sel.kpis()
    s = opt.sections

    title = opt.title or f"Verkeersanalyse – {project.name}"
    b.add(f"<h1>{_esc(title)}</h1>")
    b.add(f"<p class='muted'>Tijdelijke analyse (momentopname) · Selectie: {_esc(sel.describe())} · "
          f"Rapport gemaakt met {APP_NAME} {__version__}"
          + (f" · Opgesteld door: {_esc(opt.author)}" if opt.author else "") + "</p>")
    b.add(f"<table width='100%' class='box' cellpadding='8'><tr><td><b>{'<br>'.join(_esc(x) for x in DISCLAIMER.splitlines())}"
          "</b></td></tr></table>")

    if "overview" in s:
        v = project.video
        run = project.analysis
        b.add("<h2>Overzicht</h2>")
        b.add("<p>Dit rapport beschrijft verkeersbewegingen binnen één tijdelijke video-opname. Alle waarden zijn "
              "relatief ten opzichte van deze opname; er worden geen absolute snelheden gemeten.</p>")
        b.add(_kv_table([
            ("Project", project.name),
            ("Videobestand", v.file_name),
            ("Videoduur", fmt_duration(v.duration_s, always_hours=v.duration_s >= 3600)),
            ("Resolutie / framerate", f"{v.resolution_label} · {fmt_number(v.fps, 2)} beelden/s"),
            ("Bestandsgrootte", fmt_bytes(v.size_bytes)),
            ("Analyse uitgevoerd", fmt_datetime(run.finished_at) if run else "—"),
            ("Instelling verwachte snelheid (alleen optimalisatie)", project.settings.speed_label()),
            ("Instelling beeldbreedte (alleen optimalisatie)", project.settings.width_label()),
            ("Gevonden verkeersrichtingen",
             ", ".join(f"{d['label']} ({d['description']})" for d in res.directions) or "geen duidelijke richtingen"),
        ]))
        b.figure(C.fig_flow_map(res, sel, C.new_figure(8, 5)), 0.8)

    if "kpis" in s:
        b.add("<h2>Kerncijfers</h2>")
        rows = [(t, f"{v}  ({h})" if h else v) for t, v, h in C.kpi_text(k)]
        if k["pedestrians"]:
            rows.append(("Voetgangers (niet in voertuigtotaal)", fmt_number(k["pedestrians"])))
        if k["static_vehicles"]:
            rows.append(("Voertuigen stil gedurende gehele waarneming (apart gehouden)", fmt_number(k["static_vehicles"])))
        b.add(_kv_table(rows))

    if "heatmaps" in s:
        b.add("<h2>Heatmaps</h2>")
        b.add("<p>De heatmaps tonen waar in het beeld verkeersactiviteit plaatsvindt. Gebruikt wordt het "
              "contactpunt van een voertuig met de weg.</p>")
        for kind in ("density", "movement", "variability", "stagnation"):
            b.figure(C.fig_heatmap(res, sel, kind, C.new_figure(8, 5.2)), 0.82)

    if "vehicles" in s:
        b.add("<h2>Voertuiggegevens</h2>")
        b.figure(C.fig_intensity(sel, C.new_figure(9, 3.6)))
        b.figure(C.fig_categories(sel, C.new_figure(8, 3.4)), 0.8)
        b.figure(C.fig_directions(res, sel, C.new_figure(8, 3.4)), 0.8)
        T = res.tracks
        rows = []
        for c, n in sorted(sel.counts_by_category().items(), key=lambda kv: -kv[1]):
            rr = sel.movement_rows[T["cat"][sel.movement_rows] == c]
            per_dir = ", ".join(f"{d['label']}: {int((T['direction'][rr] == d['id']).sum())}" for d in res.directions)
            rows.append([CATEGORY_INFO[Category(c)].plural, fmt_number(n), per_dir or "—"])
        if rows:
            b.add(_grid_table(["Categorie", "Verkeersbewegingen", "Per richting"], rows))
        b.add("<p class='muted'>Bestel- en vrachtwagens worden door het detectiemodel niet betrouwbaar onderscheiden en "
              "zijn daarom samengevoegd. 'Type onbekend' betreft bewegende objecten die alleen via bewegingsdetectie "
              "zijn gevonden.</p>")

    if "movement" in s:
        b.add("<h2>Bewegingsanalyse</h2>")
        med = k["median_rel"]
        b.add(f"<p>Bewegingspatroon in deze selectie: <b>{_esc(k['uniformity_label'])}</b>"
              + (f" (spreiding {fmt_number(k['spread'], 2)}; mediaan relatieve beweging {fmt_number(med, 2)})"
                 if med == med else "") + ". Relatieve beweging 1,0 is de typische beweging binnen deze opname; "
              "warm = relatief meer beweging, koud = relatief minder.</p>")
        b.figure(C.fig_movement_series(sel, C.new_figure(9, 3.6)))
        b.figure(C.fig_movement_hist(sel, C.new_figure(8, 3.4)), 0.8)

    if "stagnation" in s:
        b.add("<h2>Stilstand en stagnatie</h2>")
        b.add(f"<p>Stilstand {fmt_number(k['stationary_share'] * 100)}% en lage beweging "
              f"{fmt_number(k['low_share'] * 100)}% van de tijd. Totale duur met wachtrij-indicatie: "
              f"{fmt_duration(k['queue_seconds'])}.</p>")
        b.figure(C.fig_presence(sel, C.new_figure(9, 3.6)))
        b.figure(C.fig_stationary_durations(sel, C.new_figure(8, 3.4)), 0.8)
        evs = [e for e in sel.events() if e["type"] in ("stagnation", "queue", "long_stop")]
        if evs:
            b.add(_grid_table(["Begin", "Einde", "Type", "Omschrijving"],
                              [[fmt_duration(e["t_start"]), fmt_duration(e["t_end"]), e["label"], e["description"]]
                               for e in evs[:40]]))
            if len(evs) > 40:
                b.add(f"<p class='muted'>… en {len(evs) - 40} andere gebeurtenissen (zie de applicatie).</p>")
        else:
            b.add("<p class='muted'>Geen stagnatie of wachtrij-indicaties van betekenis in deze selectie.</p>")

    if "methodology" in s:
        b.add("<h2>Methodologie</h2>")
        for t, text in METHODOLOGY_SECTIONS:
            b.add(f"<h3>{_esc(t)}</h3><p>{_esc(text)}</p>")
        prov = res.summary.get("provenance", {})
        q = res.summary.get("quality", {})
        det = prov.get("detector", {})
        b.add("<h3>Verantwoording van deze analyse</h3>")
        b.add(_kv_table([
            ("Softwareversie", f"{APP_NAME} {prov.get('app_version', '—')}"),
            ("Analysemethode", f"versie {prov.get('method_version', '—')}"),
            ("Detectie", det.get("description", "—")),
            ("Tracking", (prov.get("tracker") or {}).get("name", "—")),
            ("Rekeneenheid", prov.get("compute_device", "—")),
            ("Geanalyseerde beelden",
             f"{fmt_number(q.get('processed_frames', 0))} ({fmt_number(q.get('processed_share', 0) * 100)}% van alle beelden); "
             f"{q.get('refinements', 0)}× automatisch verfijnd"),
            ("Analyse gestart / voltooid", f"{fmt_datetime(prov.get('started_at', ''))} / {fmt_datetime(prov.get('finished_at', ''))}"),
            ("Opmerkingen", "; ".join(prov.get("notes", [])) or "—"),
        ]))

    if "disclaimer" in s:
        b.add("<h2>Disclaimer</h2>")
        b.add(f"<p><b>{'<br>'.join(_esc(x) for x in DISCLAIMER.splitlines())}</b></p>")
        b.add("<p>Automatische detectie is niet foutloos. De resultaten zijn afhankelijk van beeldkwaliteit, "
              "camerastandpunt en zichtbaarheid en zijn bedoeld als indicatie. Dit rapport is geen juridische of "
              "absolute snelheidsmeting.</p>")

    doc.setHtml("<html><body>" + "".join(b.parts) + "</body></html>")
    for name, img in b.images:  # after setHtml, which clears the document
        doc.addResource(QTextDocument.ImageResource, QUrl(name), img)
    _paint_pages(doc, writer, page, footer=f"{project.name} · {APP_NAME} · relatieve analyse, geen absolute snelheden")
    return path


def _paint_pages(doc: QTextDocument, writer: QPdfWriter, page, footer: str) -> None:
    """Paint the paginated document with a footer and page numbers."""
    footer_h = 28
    body_h = page.height() - footer_h
    doc.setPageSize(QSizeF(page.width(), body_h))
    n_pages = doc.pageCount()
    painter = QPainter(writer)
    try:
        font = painter.font()
        font.setPointSizeF(7.5)
        for i in range(n_pages):
            if i:
                writer.newPage()
            painter.save()
            painter.translate(0, -i * body_h)
            doc.drawContents(painter, QRectF(0, i * body_h, page.width(), body_h))
            painter.restore()
            painter.setFont(font)
            painter.setPen(QColor("#5F6B7A"))
            painter.drawLine(0, body_h + 6, page.width(), body_h + 6)
            painter.drawText(QRectF(0, body_h + 8, page.width() * 0.8, footer_h - 8),
                             Qt.AlignLeft | Qt.AlignVCenter, footer)
            painter.drawText(QRectF(0, body_h + 8, page.width(), footer_h - 8),
                             Qt.AlignRight | Qt.AlignVCenter, f"Pagina {i + 1} van {n_pages}")
    finally:
        painter.end()
