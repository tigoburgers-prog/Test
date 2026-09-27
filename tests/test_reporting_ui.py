"""Reports, exports and a smoke test of the whole UI flow (offscreen)."""

import pytest

from traffic_snapshot.analysis.pipeline import AnalysisPipeline
from traffic_snapshot.core.project_store import ProjectStore
from traffic_snapshot.core.video import probe_video


@pytest.fixture()
def analysed(home, synthetic_video):
    store = ProjectStore(home / "Projecten")
    h = store.create_project("Rapporttest", synthetic_video, probe_video(synthetic_video), copy_video=False)
    res = AnalysisPipeline(h, store, detector_override="motion").run()
    return store, store.load(h.path), res


def test_pdf_report(qapp, analysed, tmp_path):
    from traffic_snapshot.reporting.report import ReportOptions, build_report

    _store, h, res = analysed
    out = build_report(h.project, res, res.select(), tmp_path / "rapport.pdf")
    data = out.read_bytes()
    assert data.startswith(b"%PDF") and len(data) > 50_000
    small = build_report(h.project, res, res.select(0, 5), tmp_path / "kort.pdf",
                         ReportOptions({"kpis", "disclaimer"}))
    assert small.stat().st_size < out.stat().st_size


def test_exports(analysed, tmp_path):
    from traffic_snapshot.reporting import charts as C
    from traffic_snapshot.reporting.export import (
        export_events_csv,
        export_timeseries_csv,
        export_tracks_csv,
        save_figure,
    )

    _store, h, res = analysed
    sel = res.select()
    for kind in ("density", "movement"):
        p = save_figure(C.fig_heatmap(res, sel, kind), tmp_path / f"{kind}.jpg", h.project.name)
        assert p.stat().st_size > 10_000
    save_figure(C.fig_intensity(sel), tmp_path / "int.png", h.project.name)
    text = export_tracks_csv(res, sel, tmp_path / "v.csv").read_text("utf-8-sig")
    assert "geen absolute snelheden" in text and "km/h" not in text
    export_timeseries_csv(sel, tmp_path / "t.csv")
    export_events_csv(sel, tmp_path / "e.csv")


def test_ui_flow(qapp, analysed):
    from traffic_snapshot.ui.main_window import MainWindow

    store, h, res = analysed
    w = MainWindow(store)
    w.show()
    assert w.hub.model.rowCount() == 1
    w.open_project(h)
    assert w.stack.currentWidget() is w.dashboard
    d = w.dashboard
    for i in range(d.tabs.count()):
        d.tabs.setCurrentIndex(i)
        qapp.processEvents()
    for cb in d.layer_boxes.values():
        cb.setChecked(True)
    d.player.seek_seconds(6.0)
    d.player.canvas.grab()  # paints all overlay layers
    d.set_time_range(2, 9)
    assert "0:02" in d.sel_label.text()
    d.hm_kind.setCurrentIndex(1)
    d.set_region(res.coarse_region_at(0.3, 0.7))
    w.go_details()
    for i in range(w.details.tabs.count()):
        w.details.tabs.setCurrentIndex(i)
        qapp.processEvents()
    w.go_zone(res.coarse_region_at(0.3, 0.7))
    w.go_hub()
    assert w.stack.currentWidget() is w.hub
    w.close()


def test_ui_analysis_in_background(qapp, home, synthetic_video, monkeypatch):
    """New project → settings → analysis thread (stop + resume) → dashboard, all via the UI."""
    import time

    import traffic_snapshot.analysis.pipeline as pl
    from traffic_snapshot.core.models import ProjectStatus
    from traffic_snapshot.ui.main_window import MainWindow

    monkeypatch.setattr(pl, "available_models", lambda: [])  # fast motion-only analysis
    store = ProjectStore(home / "Projecten")
    w = MainWindow(store)
    w.show()
    w.go_new()
    w.new_page.load_video(str(synthetic_video))
    assert w.new_page.btn_next.isEnabled()
    w.new_page.name_edit.setText("UI-test")
    w.new_page._create()

    def wait(cond, timeout=120):
        end = time.time() + timeout
        while not cond() and time.time() < end:
            qapp.processEvents()
            time.sleep(0.01)
        assert cond()

    wait(lambda: w.stack.currentWidget() is w.settings_page)
    assert (w.handle.path / "video" / synthetic_video.name).is_file()
    w.settings_page.speed.group.button(50).click()
    w.settings_page._start()
    wait(lambda: w.analysis_page.bar.value() > 150)
    w.stop_analysis()
    wait(lambda: w.task is None)
    assert w.handle.project.status == ProjectStatus.INTERRUPTED
    assert w.analysis_page.btn_resume.isVisible()
    w.start_analysis(True)
    wait(lambda: w.task is None)
    assert w.handle.project.status == ProjectStatus.COMPLETED
    assert w.analysis_page.btn_dash.isVisible()
    w.go_dashboard()
    assert w.stack.currentWidget() is w.dashboard
    assert int(w.dashboard.kpi_cards[0].value.text()) > 5
    w.go_hub()
    assert "Analyse gereed" in w.hub.model.item(0, 4).text()
