"""Chart and heatmap rendering shared by dashboard, detail views, exports and reports.

Figures are built with the object-oriented matplotlib API (no pyplot state),
so the same function serves an on-screen canvas and a PNG/PDF export.
Every figure carries a title and a short caption so an exported image is
understandable on its own.
"""

from __future__ import annotations

import logging

import cv2
import matplotlib
import matplotlib.cm
import matplotlib.patches
import matplotlib.ticker
import numpy as np
from matplotlib import colormaps
from matplotlib.colors import Normalize
from matplotlib.figure import Figure

from traffic_snapshot.analysis.categories import CATEGORY_INFO, Category
from traffic_snapshot.analysis.results import HEATMAP_KINDS, AnalysisResults, Selection
from traffic_snapshot.core.formatting import fmt_duration, fmt_number

matplotlib.rcParams["font.family"] = ["Segoe UI", "DejaVu Sans", "sans-serif"]
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

TEXT = "#1F2933"
MUTED = "#5F6B7A"
GRID = "#E5E9F0"
PRIMARY = "#1F4E79"

HEATMAP_CMAPS = {
    "density": "YlOrRd",
    "movement": "RdYlBu_r",  # warm-koud: blue (cold, less movement) -> red (warm, more)
    "variability": "PuRd",
    "stagnation": "Reds",
}

WARM_COLD = colormaps["RdYlBu_r"]
REL_RANGE = (0.0, 2.0)  # relative movement shown on the warm-cold scale


def rel_color(rel: float) -> tuple[int, int, int]:
    """Warm-cold colour (RGB 0..255) for a relative movement value."""
    v = (min(max(rel, REL_RANGE[0]), REL_RANGE[1]) - REL_RANGE[0]) / (REL_RANGE[1] - REL_RANGE[0])
    r, g, b, _ = WARM_COLD(v)
    return int(r * 255), int(g * 255), int(b * 255)


def heatmap_range(kind: str, grid: np.ndarray) -> tuple[float, float]:
    if kind == "movement":
        return REL_RANGE
    finite = grid[np.isfinite(grid)]
    if kind == "variability":
        return 0.0, max(1.0, float(np.percentile(finite, 98)) if len(finite) else 1.0)
    top = float(np.percentile(finite, 99)) if len(finite) else 0.0
    return 0.0, max(top, 1e-3)


def heatmap_rgba(kind: str, grid: np.ndarray, size: tuple[int, int], vrange=None, max_alpha: float = 0.75) -> np.ndarray:
    """Upscaled RGBA (uint8) rendering of a grid at ``size`` = (w, h)."""
    vmin, vmax = vrange or heatmap_range(kind, grid)
    valid = np.isfinite(grid)
    g = np.where(valid, grid, vmin).astype(np.float32)
    w, h = size
    up = cv2.resize(g, (w, h), interpolation=cv2.INTER_CUBIC)
    vmask = cv2.resize(valid.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
    norm = np.clip((up - vmin) / max(vmax - vmin, 1e-9), 0, 1)
    rgba = colormaps[HEATMAP_CMAPS[kind]](norm)
    if kind == "movement":
        alpha = np.clip(vmask, 0, 1) * max_alpha
    else:
        alpha = np.clip(norm * 1.6, 0, 1) * max_alpha * np.clip(vmask, 0, 1)
    rgba[..., 3] = alpha
    return (rgba * 255).astype(np.uint8)


def _style(ax, title: str | None = None) -> None:
    ax.set_facecolor("white")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#C5CCD6")
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.grid(True, axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    if title:
        ax.set_title(title, loc="left", fontsize=10, color=TEXT, fontweight="bold")


def new_figure(w: float = 6.0, h: float = 3.2) -> Figure:
    fig = Figure(figsize=(w, h), dpi=100, facecolor="white")
    # leave room at the bottom for the context caption
    fig.set_layout_engine("constrained", rect=(0, 0.05, 1, 0.95))
    return fig


def _caption(fig: Figure, sel: Selection, extra: str = "") -> None:
    text = f"Selectie: {sel.describe()}"
    if extra:
        text += f" · {extra}"
    fig.text(0.01, 0.005, text, fontsize=7, color=MUTED, ha="left", va="bottom")


def _time_axis(ax, edges: np.ndarray) -> None:
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: fmt_duration(v)))
    ax.set_xlabel("Tijd in video (m:ss)", fontsize=8, color=MUTED)


def _no_data(ax, text: str = "Onvoldoende gegevens in deze selectie") -> None:
    ax.set_xticks([])
    ax.set_yticks([])
    ax.grid(False)
    ax.text(0.5, 0.5, text, ha="center", va="center", transform=ax.transAxes, color=MUTED, fontsize=9)


# ------------------------------------------------------------------------ heatmap
def fig_heatmap(res: AnalysisResults, sel: Selection, kind: str, fig: Figure | None = None,
                show_region: bool = True) -> Figure:
    fig = fig or new_figure(6.4, 4.2)
    fig.clear()
    ax = fig.add_subplot(111)
    bg = cv2.cvtColor(res.background, cv2.COLOR_BGR2RGB)
    h, w = bg.shape[:2]
    grid = sel.heatmap(kind)
    vmin, vmax = heatmap_range(kind, grid)
    ax.imshow((bg * 0.75 + 255 * 0.25).astype(np.uint8), extent=(0, 1, 1, 0))
    ax.imshow(heatmap_rgba(kind, grid, (w, h), (vmin, vmax)), extent=(0, 1, 1, 0))
    if show_region and sel.region is not None:
        r = sel.region
        ax.add_patch(matplotlib.patches.Rectangle((r.x0, r.y0), r.x1 - r.x0, r.y1 - r.y0, fill=False,
                                                  edgecolor=PRIMARY, linewidth=2))
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    title, desc = HEATMAP_KINDS[kind]
    ax.set_title(title, loc="left", fontsize=10, color=TEXT, fontweight="bold")
    sm = matplotlib.cm.ScalarMappable(norm=Normalize(vmin, vmax), cmap=HEATMAP_CMAPS[kind])
    cb = fig.colorbar(sm, ax=ax, fraction=0.035, pad=0.02)
    cb.ax.tick_params(labelsize=7, colors=MUTED)
    if kind == "movement":
        cb.set_ticks([0, 1, 2])
        cb.set_ticklabels(["koud\n(minder)", "typisch", "warm\n(meer)"])
    elif kind == "density":
        cb.set_label("voertuigen (gem.)", fontsize=7, color=MUTED)
    elif kind == "stagnation":
        cb.set_label("stilstaande voertuigen (gem.)", fontsize=7, color=MUTED)
    else:
        cb.set_label("spreiding", fontsize=7, color=MUTED)
    _caption(fig, sel, desc)
    return fig


# ------------------------------------------------------------------------ counts
def fig_categories(sel: Selection, fig: Figure | None = None) -> Figure:
    fig = fig or new_figure(5.0, 2.8)
    fig.clear()
    ax = fig.add_subplot(111)
    _style(ax, "Verkeersbewegingen per categorie")
    counts = sel.counts_by_category()
    items = [(c, n) for c, n in sorted(counts.items(), key=lambda kv: -kv[1]) if n > 0]
    if not items:
        _no_data(ax)
    else:
        labels = [CATEGORY_INFO[Category(c)].label for c, _ in items]
        vals = [n for _, n in items]
        colors = [CATEGORY_INFO[Category(c)].color for c, _ in items]
        y = np.arange(len(items))[::-1]
        ax.barh(y, vals, color=colors, height=0.6)
        ax.set_yticks(y, labels, fontsize=8)
        ax.grid(True, axis="x", color=GRID)
        ax.grid(False, axis="y")
        for yi, v in zip(y, vals):
            ax.text(v, yi, f" {v}", va="center", fontsize=8, color=TEXT)
        ax.set_xlim(0, max(vals) * 1.18)
    _caption(fig, sel)
    return fig


def fig_directions(res: AnalysisResults, sel: Selection, fig: Figure | None = None) -> Figure:
    fig = fig or new_figure(5.0, 2.8)
    fig.clear()
    ax = fig.add_subplot(111)
    _style(ax, "Verkeersbewegingen per richting")
    counts = sel.counts_by_direction()
    names = {d["id"]: f"{d['arrow']} {d['label']}" for d in res.directions}
    items = [(k, n) for k, n in sorted(counts.items()) if n > 0]
    if not items:
        _no_data(ax)
    else:
        labels = [names.get(k, "Overig / onbepaald") for k, _ in items]
        vals = [n for _, n in items]
        palette = ["#1F4E79", "#0E7C86", "#B54708", "#7A5AF8", "#C11574", "#475467"]
        colors = [palette[k % len(palette)] if k >= 0 else "#98A2B3" for k, _ in items]
        x = np.arange(len(items))
        ax.bar(x, vals, color=colors, width=0.6)
        ax.set_xticks(x, labels, fontsize=8)
        for xi, v in zip(x, vals):
            ax.text(xi, v, f"{v}", ha="center", va="bottom", fontsize=8)
        ax.set_ylim(0, max(vals) * 1.2)
    _caption(fig, sel, "richtingen zijn neutraal benoemd (beeldrichting, niet geografisch)")
    return fig


def fig_intensity(sel: Selection, fig: Figure | None = None, bin_s: float | None = None) -> Figure:
    fig = fig or new_figure(6.4, 2.8)
    fig.clear()
    ax = fig.add_subplot(111)
    edges, series = sel.intensity_series(bin_s)
    b = (edges[1] - edges[0]) if len(edges) > 1 else (bin_s or sel.res.bin_s)
    _style(ax, f"Verkeersintensiteit (nieuwe voertuigen per {fmt_duration(b) if b >= 60 else f'{b:g} s'})")
    if not series:
        _no_data(ax)
    else:
        bottom = np.zeros(len(edges))
        for c in sorted(series):
            info = CATEGORY_INFO[Category(c)]
            ax.bar(edges + b / 2, series[c], width=b * 0.9, bottom=bottom, color=info.color, label=info.plural)
            bottom += series[c]
        ax.legend(fontsize=7, frameon=False, ncol=3, loc="upper left")
        ax.set_ylim(0, max(1, bottom.max()) * 1.3)
        ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    _time_axis(ax, edges)
    ax.set_xlim(sel.t0, sel.t1)
    _caption(fig, sel)
    return fig


def fig_presence(sel: Selection, fig: Figure | None = None) -> Figure:
    fig = fig or new_figure(6.4, 2.8)
    fig.clear()
    ax = fig.add_subplot(111)
    _style(ax, "Voertuigdichtheid en stilstand door de tijd")
    edges, present, stationary, queue = sel.presence_series()
    if len(edges) == 0 or present.max() <= 0:
        _no_data(ax)
    else:
        b = edges[1] - edges[0] if len(edges) > 1 else sel.res.bin_s
        x = edges + b / 2
        ax.fill_between(x, present, color="#9DB3C8", alpha=0.5, step="mid", label="Aanwezig (gem.)")
        ax.step(x, present, where="mid", color=PRIMARY, linewidth=1.2)
        ax.step(x, stationary, where="mid", color="#B42318", linewidth=1.4, label="Stilstaand (gem.)")
        if queue.max() > 0:
            ax.step(x, queue, where="mid", color="#EA580C", linewidth=1.2, linestyle="--",
                    label="In wachtrij-indicatie (gem.)")
        ax.set_ylabel("voertuigen", fontsize=8, color=MUTED)
        ax.legend(fontsize=7, frameon=False, ncol=3, loc="upper left")
        ax.set_ylim(0, max(1.0, present.max()) * 1.35)
    _time_axis(ax, edges)
    ax.set_xlim(sel.t0, sel.t1)
    _caption(fig, sel)
    return fig


# ------------------------------------------------------------------------ movement
def fig_movement_series(sel: Selection, fig: Figure | None = None) -> Figure:
    fig = fig or new_figure(6.4, 2.8)
    fig.clear()
    ax = fig.add_subplot(111)
    _style(ax, "Relatieve bewegingssnelheid door de tijd")
    edges, med, spread = sel.movement_series()
    ok = np.isfinite(med)
    if ok.sum() < 1:
        _no_data(ax)
    else:
        b = edges[1] - edges[0] if len(edges) > 1 else sel.res.bin_s
        x = edges + b / 2
        colors = [np.array(rel_color(v)) / 255 for v in np.nan_to_num(med, nan=1.0)]
        ax.bar(x[ok], med[ok], width=b * 0.9, color=[c for c, k in zip(colors, ok) if k])
        ax.axhline(1.0, color=MUTED, linewidth=0.8, linestyle="--")
        ax.text(sel.t0, 1.02, " typisch voor deze opname", fontsize=7, color=MUTED, va="bottom")
        ax.set_ylabel("relatieve beweging", fontsize=8, color=MUTED)
        ax.set_ylim(0, max(2.0, float(np.nanmax(med)) * 1.15))
    _time_axis(ax, edges)
    ax.set_xlim(sel.t0, sel.t1)
    _caption(fig, sel, "mediaan van bewegende voertuigen; warm = relatief meer, koud = relatief minder beweging")
    return fig


def fig_movement_hist(sel: Selection, fig: Figure | None = None) -> Figure:
    fig = fig or new_figure(5.0, 2.8)
    fig.clear()
    ax = fig.add_subplot(111)
    _style(ax, "Verdeling relatieve beweging")
    o = sel.res.obs
    idx = sel.idx
    m = (o["cat"][idx] != Category.PEDESTRIAN) & ~o["static"][idx]
    rel = np.clip(o["rel"][idx][m], 0, 3)
    w = o["dur"][idx][m]
    if len(rel) < 5:
        _no_data(ax)
    else:
        bins = np.linspace(0, 3, 31)
        hist, _ = np.histogram(rel, bins=bins, weights=w)
        share = hist / max(hist.sum(), 1e-9) * 100
        centers = (bins[:-1] + bins[1:]) / 2
        ax.bar(centers, share, width=0.095, color=[np.array(rel_color(c)) / 255 for c in centers])
        ax.set_xlabel("relatieve beweging (1,0 = typisch voor deze opname)", fontsize=8, color=MUTED)
        ax.set_ylabel("% van de tijd", fontsize=8, color=MUTED)
        ax.axvline(0.4, color=MUTED, linewidth=0.8, linestyle=":")
        ax.text(0.2, ax.get_ylim()[1] * 0.92, "lage\nbeweging", fontsize=7, color=MUTED, ha="center", va="top")
    _caption(fig, sel)
    return fig


def fig_stationary_durations(sel: Selection, fig: Figure | None = None) -> Figure:
    fig = fig or new_figure(5.0, 2.8)
    fig.clear()
    ax = fig.add_subplot(111)
    _style(ax, "Duur van stilstandsmomenten")
    eps = sel.stationary_episodes(min_s=1.0)
    if not eps:
        _no_data(ax, "Geen stilstand van betekenis in deze selectie")
    else:
        d = np.array([e["duration"] for e in eps])
        edges = [1, 3, 5, 10, 20, 30, 60, 120, max(121, d.max() + 1)]
        hist, _ = np.histogram(d, bins=edges)
        labels = ["1–3 s", "3–5 s", "5–10 s", "10–20 s", "20–30 s", "30–60 s", "1–2 min", "> 2 min"]
        ax.bar(np.arange(len(hist)), hist, color="#B42318", width=0.6)
        ax.set_xticks(np.arange(len(hist)), labels, fontsize=7, rotation=0)
        ax.set_ylabel("aantal", fontsize=8, color=MUTED)
        ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    _caption(fig, sel, "voertuigen die de gehele waarneming stilstaan zijn niet meegenomen")
    return fig


def fig_flow_map(res: AnalysisResults, sel: Selection, fig: Figure | None = None) -> Figure:
    """Background image with the dominant flows drawn as arrows."""
    fig = fig or new_figure(6.4, 4.2)
    fig.clear()
    ax = fig.add_subplot(111)
    bg = cv2.cvtColor(res.background, cv2.COLOR_BGR2RGB)
    ax.imshow(bg, extent=(0, 1, 1, 0))
    T = res.tracks
    rows = sel.movement_rows
    palette = ["#1F4E79", "#0E7C86", "#B54708", "#7A5AF8", "#C11574", "#475467"]
    for d in res.directions:
        m = rows[T["direction"][rows] == d["id"]]
        if len(m) == 0:
            continue
        for r in m[:150]:
            ax.plot([T["x0"][r], T["x1"][r]], [T["y0"][r], T["y1"][r]], color=palette[d["id"] % 6], alpha=0.25,
                    linewidth=1)
        x0, y0 = float(np.median(T["x0"][m])), float(np.median(T["y0"][m]))
        x1, y1 = float(np.median(T["x1"][m])), float(np.median(T["y1"][m]))
        ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                    arrowprops=dict(arrowstyle="-|>", color=palette[d["id"] % 6], lw=3, mutation_scale=18))
        ax.text(x0, y0, f" {d['label']} ({len(m)})", color="white", fontsize=8, fontweight="bold",
                bbox=dict(facecolor=palette[d["id"] % 6], edgecolor="none", pad=2, alpha=0.9))
    ax.set_xlim(0, 1)
    ax.set_ylim(1, 0)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title("Verkeersstromen (richtingen in beeld)", loc="left", fontsize=10, color=TEXT, fontweight="bold")
    _caption(fig, sel, "lijnen: begin- en eindpunt per voertuig; pijl: typische route per richting")
    return fig


def kpi_text(k: dict) -> list[tuple[str, str, str]]:
    """(label, value, hint) tuples shared by dashboard cards and reports."""
    rel = k["median_rel"]
    return [
        ("Verkeersbewegingen", fmt_number(k["movements"]), f"{fmt_number(k['movements_per_min'], 1)} per minuut"),
        ("Voertuigdichtheid", fmt_number(k["avg_present"], 1), f"gem. tegelijk in beeld (max. {fmt_number(k['max_present'], 1)})"),
        ("Bewegingsspreiding", k["uniformity_label"].capitalize(),
         "relatieve beweging: typisch = 1,0" if rel == rel else "—"),
        ("Stilstand", f"{fmt_number(k['stationary_share'] * 100)}%", f"van de tijd; lage beweging {fmt_number(k['low_share'] * 100)}%"),
        ("Wachtrij-indicatie", fmt_duration(k["queue_seconds"]) if k["queue_seconds"] else "Geen",
         "totale duur (m:ss)"),
        ("Gebeurtenissen", fmt_number(k["events"]), f"{k['n_directions']} verkeersrichting(en)"),
    ]
