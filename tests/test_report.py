import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from slmcp.adapters.memory import MemoryAdapter
from slmcp.config import build_adapter, load_config
from slmcp.report import build_report_inputs, main
from slmcp.schema import from_dict
from slmcp.trend import entity_trend

SAMPLE = Path(__file__).parent.parent / "examples" / "posts_sample.csv"


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.yaml"
    config = {"data_source": {"adapter": "csv", "path": str(SAMPLE)},
              "model": {"endpoint": "http://localhost:8000/v1", "name": "unused"}, "today": "2026-11-10"}
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def post(day, text, path):
    return from_dict({"date": f"{day}T09:00:00+07:00", "text": text, "source": "example.com",
                      "platform": "news", "url": f"https://example.com/{path}"})


def test_facts_and_outline_preserve_the_daily_counts_and_source_links(config_path):
    posts = [post("2026-09-01", "Krakatau smoke", "smoke"), post("2026-09-02", "Krakatau ash", "ash"),
             post("2026-09-02", "Krakatau damage", "damage"), post("2026-09-03", "Unrelated", "other")]
    report = build_report_inputs(MemoryAdapter(posts), load_config(config_path), " krakatau ",
                                 "2026-09-01", "2026-09-03", snapshot="example")

    assert report["query"] == {"keyword": "krakatau", "start_date": "2026-09-01", "end_date": "2026-09-03",
                               "timezone": "Asia/Jakarta", "snapshot": "example"}
    facts = report["facts"]
    assert report["data_available"] and facts["total"] == 3
    assert facts["daily_trend"] == [{"date": "2026-09-01", "count": 1}, {"date": "2026-09-02", "count": 2},
                                    {"date": "2026-09-03", "count": 0}]
    assert facts["peak"] == {"at": "2026-09-02", "count": 2}
    assert {story["url"] for story in facts["top_stories"]} == {p.url for p in posts[:3]}
    assert facts["change"] is None
    assert any("previous period" in warning.lower() for warning in report["warnings"])
    assert set(report["fact_sources"]) == set(facts)
    assert report["fact_sources"]["top_stories"] == "entity_trend.top_stories"
    assert {ref for section in report["outline"] for ref in section["uses"]} == {
        "query", "warnings", "facts.total", "facts.daily_trend", "facts.peak", "facts.top_stories",
    }


def test_report_can_include_a_covered_comparison(config_path):
    config = load_config(config_path)
    report = build_report_inputs(build_adapter(config), config, "banjir", "2026-11-05", "2026-11-08",
                                 snapshot="synthetic")
    assert report["facts"]["change"]["previous_start"] == "2026-11-01"
    assert "facts.change" in report["outline"][1]["uses"]


def test_local_report_retains_daily_counts_beyond_the_chat_budget(config_path):
    first = date(2025, 10, 1)
    last = first + timedelta(days=365)
    adapter = MemoryAdapter([post(first, "Krakatau ash", "ash"), post(last, "Krakatau smoke", "smoke")])
    config = load_config(config_path)
    chat_trend = entity_trend(adapter, "krakatau", first, last, today=config.today, max_tokens=500)
    report = build_report_inputs(adapter, config, "krakatau", first, last, snapshot="example")

    assert chat_trend["trend"]["removed"] is True
    assert len(report["facts"]["daily_trend"]) == 366
    assert report["facts"]["total"] == 2


@pytest.mark.parametrize("start, end", [("2026-08-01", "2026-08-14"), ("2026-08-31", "2026-09-02")])
def test_outside_and_partly_covered_ranges_have_no_report_metrics(config_path, start, end):
    adapter = MemoryAdapter([post("2026-09-01", "Krakatau ash", "ash"),
                             post("2026-09-03", "Krakatau smoke", "smoke")])
    config = load_config(config_path)
    report = build_report_inputs(adapter, config, "krakatau", start, end, snapshot="example")
    assert report["data_available"] is False and report["warnings"]
    assert report["facts"] == report["fact_sources"] == {}
    assert [section["title"] for section in report["outline"]] == ["Data coverage"]


def test_cli_distinguishes_zero_hits_from_missing_data(config_path, capsys):
    status = main(["--config", str(config_path), "--keyword", "no-example-headline-matches-this",
                   "--start", "2026-11-01", "--end", "2026-11-09", "--snapshot", "synthetic"])
    report = json.loads(capsys.readouterr().out)
    assert status == 0 and report["data_available"]
    assert report["facts"]["total"] == 0
    assert report["facts"]["peak"] is None and report["facts"]["top_stories"] == []
    assert any("No posts found" in warning for warning in report["warnings"])
