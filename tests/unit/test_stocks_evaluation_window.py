"""Offline evaluations read every stored day (not just what collection re-asks Massive for), memory allowing."""

from datetime import timedelta

from desk.core.config import load_config
from desk.stocks import validation as val
from tests.conftest import REPO
from tests.stocks_world import make_world

SETTINGS = load_config(REPO / "config").settings.stocks


def test_evaluation_window_defaults_to_every_stored_day_beyond_the_collection_window(tmp_path):
    w = make_world(tmp_path, n=8)
    try:
        short = SETTINGS.model_copy(update={"history_days": 200})
        assert short.evaluation_history_days == 0
        assert val.evaluation_window(w.market, short, available_kb=64_000_000) == (w.days[0], None)
        capped = short.model_copy(update={"evaluation_history_days": 400})
        start, note = val.evaluation_window(w.market, capped, available_kb=64_000_000)
        assert note is None and start >= w.days[-1] - timedelta(days=400) and start > w.days[0]
    finally:
        w.close()


def test_memory_keeps_the_newest_days_and_never_drops_below_the_collection_window(tmp_path):
    w = make_world(tmp_path, n=8)
    try:
        short = SETTINGS.model_copy(update={"history_days": 200})
        start, note = val.evaluation_window(w.market, short, available_kb=val.EVALUATION_RESERVE_KB)
        floor = w.days[-1] - timedelta(days=200)
        assert note and "memory" in note and start >= floor and start == min(d for d in w.days if d >= floor)
        rows = w.market.conn.execute("SELECT SUM(rows) FROM stk_bar_days WHERE rows > 0").fetchone()[0]
        room = val.EVALUATION_RESERVE_KB + rows * val.EVALUATION_BYTES_PER_BAR // 1024 + 1
        assert val.evaluation_window(w.market, short, available_kb=room) == (w.days[0], None)
    finally:
        w.close()


def test_evidence_and_offline_evaluation_load_the_evaluation_window_not_history_days(tmp_path, monkeypatch):
    import json

    from desk.stocks import evidence_job, runner
    from desk.stocks.prices import load_panel

    w = make_world(tmp_path, n=8)
    seen = []

    def spy(market, since, until=None, *args, **kwargs):
        seen.append(since)
        return load_panel(market, since, until, *args, **kwargs)

    import shutil

    config = tmp_path / "config"
    shutil.copytree(REPO / "config", config)
    text = (REPO / "config" / "default.toml").read_text().replace("history_days = 730", "history_days = 200", 1)
    (config / "default.toml").write_text(text)
    w.paths = w.paths.__class__(w.paths.home, config, w.paths.secrets_file)
    monkeypatch.setattr("desk.stocks.prices.load_panel", spy)
    monkeypatch.setattr(runner, "load_panel", spy)
    monkeypatch.setattr(val, "rebuild", lambda *a, **k: [])
    monkeypatch.setattr(val, "run", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(val, "_available_kb", lambda: None)
    try:
        loaded = load_config(config)
        assert loaded.settings.stocks.history_days == 200
        runner.evaluate(w.paths, loaded)
        evidence_job.evaluate(w.paths)
        assert seen == [w.days[0], w.days[0]]
        report = json.loads((w.paths.home / "stocks-evidence-report.json").read_text())
        assert report["prices"]["first"] == w.days[0].isoformat() and report["prices"]["memory_note"] is None
    finally:
        w.close()
