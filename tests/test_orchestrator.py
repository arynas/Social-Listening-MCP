import json

import pytest

from slmcp import orchestrator
from slmcp.analyst import NewsRequest, PilotError, make_brief, reconcile
from slmcp.orchestrator import approve_outline, read_json, start_run, write_json

REQUEST = NewsRequest("Krakatau", "2026-09-01", "2026-09-04")
PEAK = {"at": "2026-09-02", "count": 2}
STORY = {"source": "example.test", "text": "Krakatau in the news", "url": "https://example.test/krakatau"}


def tool_results():
    common = {**REQUEST.tool_args(), "data_available": True, "total": 4,
              "must_tell_user": ["Headlines only."]}
    trend = {**common, "peak": PEAK, "change": None, "top_stories": [STORY], "trend": {
        "unit": "day", "labels": [f"2026-09-0{day}" for day in range(1, 5)], "total": [0, 2, 2, 0],
    }}
    return common, trend


def test_tool_evidence_reconciles_daily_series_and_first_tied_peak():
    result = reconcile(REQUEST, *tool_results())
    assert result["facts"]["peak"] == PEAK
    assert sum(row["count"] for row in result["facts"]["daily_trend"]) == result["facts"]["total"] == 4
    assert result["facts"]["top_stories"] == [STORY]


def test_inconsistent_tool_total_stops_before_analysis():
    recap, trend = tool_results()
    trend["total"] = 5
    with pytest.raises(PilotError, match="total disagrees"):
        reconcile(REQUEST, recap, trend)


def test_uncovered_period_abstains_without_a_model_call():
    missing = {"keyword": REQUEST.keyword, "data_available": False,
               "asked_period": {"start": REQUEST.start, "end": REQUEST.end},
               "data_covers": {"start": "2026-09-05", "end": "2026-09-30"},
               "must_tell_user": ["Outside coverage."]}
    evidence = reconcile(REQUEST, missing, missing)
    brief = make_brief(evidence, lambda _: pytest.fail("unexpected model call"), "example-model")
    assert evidence["facts"] == {}
    assert brief["status"] == "abstained" and brief["model"] is None


@pytest.mark.parametrize(("text", "references", "error"), [
    ("Reported 999 headlines.", ["volume"], "embedded figures"),
    ("Coverage rose.", ["invented"], "outside the MCP evidence"),
])
def test_analyst_rejects_written_figures_or_invented_evidence(text, references, error):
    evidence = reconcile(REQUEST, *tool_results())
    reply = json.dumps({"findings": [{"text": text, "evidence": references}]})
    with pytest.raises(PilotError, match=error):
        make_brief(evidence, lambda _: reply, "example-model")


def test_analyst_can_correct_a_malformed_first_reply():
    evidence = reconcile(REQUEST, *tool_results())
    replies = iter(("This is not JSON", '{"findings":[{"text":"Coverage peaked.","evidence":["trend"]}]}'))
    brief = make_brief(evidence, lambda _: next(replies), "example-model")
    assert brief["attempts"] == 2


@pytest.fixture
def staged_run(monkeypatch, tmp_path):
    folder = tmp_path / "pilot"
    evidence = reconcile(REQUEST, *tool_results())
    inputs = {**evidence, "request": {"keyword": REQUEST.keyword, "start": REQUEST.start, "end": REQUEST.end},
              "query": {"snapshot": orchestrator.SNAPSHOT, "timezone": orchestrator.PILOT_TZ}}
    commands = []

    async def collect(*args):
        return evidence

    def kit(name, command, *args):
        commands.append(command)
        out = args[-1]
        out.mkdir()
        write_json(out / "report-inputs.json", inputs)
        if command == "outline":
            (out / "outline.md").write_text("Review this outline")
        else:
            (out / "report.pptx").write_bytes(b"PK sample")
            (out / "report.pdf").write_bytes(b"%PDF- sample")
            write_json(out / "check_report.json", {"status": "pass", "issues": [], "expected": {
                "slides": 4, "data_available": True, "total": 4, "peak": PEAK, "daily_chart_points": 4,
                "headline_links": 1,
            }})

    monkeypatch.setattr(orchestrator, "collect", collect)
    monkeypatch.setattr(orchestrator, "kit_command", kit)
    reply = json.dumps({"findings": [{"text": "Coverage clustered near its peak.", "evidence": ["trend"]}]})
    trace = start_run(folder, REQUEST, "http://example.test/mcp", "kit", tmp_path,
                      lambda _: reply, "example-model")
    return folder, trace, commands


def test_outline_approval_builds_one_reviewed_draft(staged_run, tmp_path):
    folder, trace, commands = staged_run
    assert trace["status"] == "awaiting_outline_approval"
    assert commands == ["outline"] and not (folder / "report").exists()
    reviewed = approve_outline(folder, "kit", tmp_path)
    assert reviewed["status"] == "awaiting_final_approval" and reviewed["run_id"] == trace["run_id"]
    assert commands == ["outline", "build"]
    assert approve_outline(folder, "kit", tmp_path) == reviewed
    assert commands == ["outline", "build"]
    assert read_json(folder / "report/analysis.json") == read_json(folder / "analysis.json")


def test_changed_outline_requires_a_new_review(staged_run, tmp_path):
    folder, _, commands = staged_run
    (folder / "outline/outline.md").write_text("Changed outline")
    with pytest.raises(PilotError, match="approved inputs or analysis changed"):
        approve_outline(folder, "kit", tmp_path)
    assert commands == ["outline"] and not (folder / "report").exists()


def test_tool_failure_is_recorded_and_never_reaches_renderer(monkeypatch, tmp_path):
    async def broken_tool(*args):
        raise PilotError("news tool unavailable")

    monkeypatch.setattr(orchestrator, "collect", broken_tool)
    run = tmp_path / "failed"
    with pytest.raises(PilotError, match="news tool unavailable"):
        start_run(run, REQUEST, "http://example.test/mcp", "kit", tmp_path, None, "example-model")
    trace = read_json(run / "trace.json")
    assert trace["status"] == "failed"
    assert trace["steps"][-1]["stage"] == "data" and trace["steps"][-1]["event"] == "failed"
    assert not (run / "report").exists()
