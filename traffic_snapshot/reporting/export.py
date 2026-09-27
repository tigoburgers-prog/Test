"""Local export of figures, screenshots and data, always with context."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
from matplotlib.figure import Figure

from traffic_snapshot import APP_NAME
from traffic_snapshot.analysis.categories import CATEGORY_INFO, Category
from traffic_snapshot.analysis.results import AnalysisResults, Selection
from traffic_snapshot.core.formatting import fmt_duration

CONTEXT_NOTE = "Relatieve analyse van één tijdelijke opname · geen absolute snelheden · lokaal verwerkt"


def context_line(project_name: str) -> str:
    return f"{project_name} · {APP_NAME} · {CONTEXT_NOTE}"


def save_figure(fig: Figure, path: Path, project_name: str, dpi: int = 160) -> Path:
    """Save a figure with a context footer (project + disclaimer)."""
    path = Path(path)
    txt = fig.text(0.99, 0.005, context_line(project_name), fontsize=6.5, color="#5F6B7A", ha="right", va="bottom")
    try:
        kwargs = {"dpi": dpi, "facecolor": "white"}
        if path.suffix.lower() in (".jpg", ".jpeg"):
            kwargs["pil_kwargs"] = {"quality": 92}
        fig.savefig(path, **kwargs)
    finally:
        txt.remove()
    return path


def export_tracks_csv(res: AnalysisResults, sel: Selection, path: Path) -> Path:
    T = res.tracks
    dirs = {d["id"]: d["label"] for d in res.directions}
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["# " + CONTEXT_NOTE])
        w.writerow(["# Selectie: " + sel.describe()])
        w.writerow(["voertuig_id", "categorie", "eerste_waarneming_s", "laatste_waarneming_s", "richting",
                    "verkeersbeweging", "stil_gehele_waarneming", "gem_relatieve_beweging", "stilstand_s"])
        for r in sel.vehicle_rows:
            w.writerow([
                int(T["id"][r]), CATEGORY_INFO[Category(int(T["cat"][r]))].label,
                f"{T['t0'][r]:.2f}".replace(".", ","), f"{T['t1'][r]:.2f}".replace(".", ","),
                dirs.get(int(T["direction"][r]), "onbepaald"),
                "ja" if T["moving"][r] else "nee", "ja" if T["static"][r] else "nee",
                f"{T['mean_rel'][r]:.2f}".replace(".", ","), f"{T['stationary_s'][r]:.1f}".replace(".", ","),
            ])
    return path


def export_timeseries_csv(sel: Selection, path: Path) -> Path:
    edges, series = sel.intensity_series()
    _, present, stationary, queue = sel.presence_series()
    _, med, spread = sel.movement_series()
    cats = sorted(series)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["# " + CONTEXT_NOTE])
        w.writerow(["# Selectie: " + sel.describe()])
        w.writerow(["tijd_start", "nieuwe_verkeersbewegingen"] + [f"nieuw_{CATEGORY_INFO[Category(c)].label}" for c in cats]
                   + ["gem_aanwezig", "gem_stilstaand", "gem_in_wachtrij_indicatie", "mediaan_relatieve_beweging",
                      "spreiding_relatieve_beweging"])
        total = sum(series.values()) if series else np.zeros(len(edges))
        for i, e in enumerate(edges):
            def num(v, d=2):
                return "" if not np.isfinite(v) else f"{v:.{d}f}".replace(".", ",")
            w.writerow([fmt_duration(e), int(total[i])] + [int(series[c][i]) for c in cats]
                       + [num(present[i]), num(stationary[i]), num(queue[i]), num(med[i]), num(spread[i])])
    return path


def export_events_csv(sel: Selection, path: Path) -> Path:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["# " + CONTEXT_NOTE])
        w.writerow(["type", "start", "einde", "omschrijving"])
        for e in sel.events():
            w.writerow([e["label"], fmt_duration(e["t_start"]), fmt_duration(e["t_end"]), e["description"]])
    return path
