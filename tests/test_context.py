import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from test_final_review import bundle

from slmcp.analyst import NewsRequest, PilotError, make_brief
from slmcp.context import brief_markdown, context_notices, source_passage, usable_sources
from slmcp.context_mcp import load_passages, search_passages
from slmcp.final_review import FinalReviews, create_server
from slmcp.orchestrator import Run, digests, require_approved_inputs
from slmcp.reviewer import check_analysis

EXAMPLES = Path(__file__).parents[1] / "examples" / "context"
REQUEST = NewsRequest("Krakatau", "2026-09-01", "2026-09-14")
ORIGIN = "http://127.0.0.1:8771"


def test_conflict_requires_same_event_date_and_cites_both_records():
    web, other = load_passages(EXAMPLES / "web-krakatau.json")
    opposite, = load_passages(EXAMPLES / "kb-conflict-krakatau.json")
    sources = {"web-1": web, "web-2": other, "kb-1": opposite}
    services = {"web": "ok", "kb": "ok"}
    notices = context_notices(services, sources)
    assert len(notices) == 1
    assert notices[0]["kind"] == "conflict" and notices[0]["evidence"] == ["web-1", "kb-1"]
    context = {"services": services, "sources": sources, "notices": notices}
    assert usable_sources(context) == {"web-2": other}
    opposite["observation"]["date"] = "2026-09-07"
    assert context_notices(services, sources) == []


def test_retriever_preserves_historical_scope_and_rejects_unrelated_or_unsafe_sources():
    archive, = load_passages(EXAMPLES / "kb-krakatau.json")
    future = {**archive, "date": "2026-10"}
    unrelated = {**archive, "title": "Merapi", "excerpt": "Aktivitas gunung Merapi"}
    assert search_passages([archive, future, unrelated], REQUEST) == [archive]
    with pytest.raises(ValueError, match="citation URL"):
        source_passage({**archive, "url": "javascript:alert(1)"})


def snapshot():
    return {"data_available": True, "warnings": [], "facts": {"total": 1976, "daily_trend": [],
            "peak": {"at": "2026-09-06", "count": 733}, "change": None, "top_stories": []}}


def test_citations_are_generated_and_unretrieved_references_never_pass_review(tmp_path):
    archive, = load_passages(EXAMPLES / "kb-krakatau.json")
    context = {"services": {"web": "unavailable", "kb": "ok"}, "sources": {"kb-1": archive}, "notices": []}
    context["notices"] = context_notices(context["services"], context["sources"])
    evidence = {**snapshot(), "context": context}
    statement = {"text": "Laporan historis merekam runtuhnya lereng.", "evidence": ["kb-1"]}
    raw = {"findings": [{"text": "Ada liputan.", "evidence": ["volume"]}], "context_findings": [statement]}
    brief = make_brief(evidence, lambda _: json.dumps(raw), "pilot")
    markdown = brief_markdown(brief)
    assert "[kb-1](#kb-1)" in markdown and archive["url"] in markdown
    assert "Pencarian web tidak tersedia" in markdown
    assert "2019-08" in markdown and archive["excerpt"] in markdown

    bogus = {**statement, "evidence": ["web-1"]}
    brief["context"]["findings"] = [bogus]
    (tmp_path / "analysis.json").write_text(json.dumps(brief))
    findings = check_analysis(tmp_path, evidence, None)
    assert len(findings) == 1 and "outside the MCP evidence" in findings[0]["message"]


def test_context_tampering_blocks_outline_approval(tmp_path):
    run = Run.create(tmp_path / "run", REQUEST)
    path = run.folder / "context.json"
    path.write_text("{}")
    run.trace["outline_digests"] = digests(run.folder, ("context.json",))
    path.write_text("[]")
    with pytest.raises(PilotError, match="approved inputs"):
        require_approved_inputs(run)


def test_final_review_serves_and_binds_the_cited_brief(tmp_path):
    archive, = load_passages(EXAMPLES / "kb-krakatau.json")
    context = {"services": {"kb": "ok"}, "sources": {"kb-1": archive}, "notices": []}
    folder = bundle(tmp_path / "checked")
    prepared = folder / "report"
    analysis = json.loads((prepared / "analysis.json").read_text())
    analysis["context"] = {"evidence": context, "findings": [
        {"text": "Ada bukti dan konteks terdahulu.", "evidence": ["kb-1"]},
    ]}
    (prepared / "analysis.json").write_text(json.dumps(analysis))
    (prepared / "analysis.md").write_text(brief_markdown(analysis))
    trace = json.loads((folder / "trace.json").read_text())
    trace["steps"][-1]["artifacts"] = digests(folder, (f"report/{p.name}" for p in prepared.iterdir()))
    (folder / "trace.json").write_text(json.dumps(trace))

    store = FinalReviews(tmp_path / "review.sqlite3")
    report = store.publish(folder)
    app = create_server(store, ORIGIN).streamable_http_app()
    with TestClient(app, base_url=ORIGIN) as browser:
        url = f"/final-review/{report['token']}"
        page = browser.get(url)
        assert 'href="#kb-1"' in page.text
        assert archive["url"] in page.text
        assert browser.get(url + "/analysis.md").text == brief_markdown(analysis)
    approved = store.decide(report["token"], report["digest"], "approve", "")
    assert approved["receipt"]["artifacts"]["report/analysis.md"] == report["manifest"]["report/analysis.md"]
    (prepared / "analysis.md").write_text("Tampered")
    with pytest.raises(PilotError, match="cited brief"):
        store.publish(folder)
