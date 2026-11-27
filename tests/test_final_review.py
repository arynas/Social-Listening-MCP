import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
from starlette.testclient import TestClient
from test_report_jobs import call

from slmcp.analyst import PilotError
from slmcp.final_review import PENDING, FinalReviews, ReviewConflict, create_server, revision_request

ORIGIN = "http://127.0.0.1:8771"


def bundle(folder, *, content="first", keyword="Krakatau", revision=None, covered=True, passed=True):
    output = folder / "report"
    output.mkdir(parents=True)
    request = {"keyword": keyword, "start": "2026-09-01", "end": "2026-09-14"}
    scope = {"snapshot": "data-2026-10-v2", "timezone": "Asia/Jakarta"}
    checks = {"status": "pass" if passed else "fail", "issues": [],
              "expected": {"total": 1976 if covered else None, "slides": 4 if covered else 1,
                           "headline_links": 3 if covered else 0, "data_available": covered}}
    for name, data in {
        "analysis.json": {"findings": [{"text": f"<b>{content}</b>", "evidence": ["coverage"]}]},
        "review.json": {"status": "pass", "findings": [], "s2_checks": checks, "analysis_reviewed": True},
        "check_report.json": checks,
        "report-inputs.json": {"request": request, "query": scope},
    }.items():
        (output / name).write_text(json.dumps(data))
    for name in ("report.pdf", "report.pptx"):
        (output / name).write_text(f"{content} {name}")
    artifacts = {f"report/{p.name}": hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()}
    decision = {"stage": "orchestrator", "event": "decision", "status": PENDING, "artifacts": artifacts}
    trace = {**scope, "request": request, "status": PENDING, "run_id": folder.name, "steps": [decision]}
    if revision:
        trace["final_review_revision"] = revision
    (folder / "trace.json").write_text(json.dumps(trace))
    return folder


def event_kinds(store, review_id):
    with closing(store.connect()) as conn:
        return [row["kind"] for row in conn.execute(
            "SELECT kind FROM events WHERE review_id = ? ORDER BY id", (review_id,)
        )]


def test_rejected_report_has_no_delivery_and_mcp_is_read_only(tmp_path):
    store = FinalReviews(tmp_path / "review.sqlite3")
    report = store.publish(bundle(tmp_path / "clean"))
    server = create_server(store, ORIGIN)
    found = call(server, "get_analyst_final_review", {"review_id": report["review_id"]})
    assert found["state"] == PENDING and found["receipt"] is None
    url = found["review_url"]
    with TestClient(server.streamable_http_app(), base_url=ORIGIN) as browser:
        page = browser.get(url)
        assert "Version 1 · Awaiting final approval" in page.text
        assert "&lt;b&gt;first&lt;/b&gt;" in page.text
        assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
        assert browser.get(url + "/report.pdf").content == b"first report.pdf"
        assert browser.get(url + "/analysis.json").status_code == 404
        assert browser.get(url + "/report.pptx").headers["Content-Disposition"].startswith("attachment")
        form = {"action": "reject", "digest": report["digest"], "note": "<script>note</script>"}
        assert browser.post(url, data=form, headers={"Origin": "null"}).status_code == 403
        assert browser.post(url, data=form, headers={"Origin": "https://example.invalid"}).status_code == 403
        wrong_digest = browser.post(url, data={**form, "digest": "wrong"}, headers={"Origin": ORIGIN})
        assert wrong_digest.status_code == 409
        rejected = browser.post(url, data=form, headers={"Origin": ORIGIN})
        assert rejected.status_code == 200
        assert "Version 1 · Rejected" in rejected.text
        assert "&lt;script&gt;note&lt;/script&gt;" in rejected.text
        assert "Approve report" not in rejected.text
        retry = browser.post(url, data={**form, "action": "approve"}, headers={"Origin": ORIGIN})
        assert retry.status_code == 409
    assert store.get(review_id=report["review_id"])["receipt"] is None
    assert event_kinds(store, report["review_id"]) == ["published", "rejected", "action_checked"]
    entry, = store.action_trace(report["review_id"])
    assert entry["decision"] == "block" and entry["code"] == "review_closed"


def test_revision_requires_current_checked_bundle_and_delivers_only_once(tmp_path):
    db = tmp_path / "review.sqlite3"
    store = FinalReviews(db)
    initial = bundle(tmp_path / "first")
    first = store.publish(initial)
    case = first["review_id"]
    with pytest.raises(ValueError, match="give a reason"):
        store.decide(first["token"], first["digest"], "changes", "")
    change = store.decide(first["token"], first["digest"], "changes", "Add verified headline detail.")
    assert change["state"] == "changes_requested" and change["receipt"] is None
    with pytest.raises(ReviewConflict, match="no longer"):
        store.decide(first["token"], first["digest"], "approve", "")
    with pytest.raises(ReviewConflict, match="recorded human"):
        store.publish(bundle(tmp_path / "unlinked"), case)
    with pytest.raises(ReviewConflict, match="same report scope"):
        store.publish(bundle(tmp_path / "scope", revision=revision_request(change), keyword="Other"), case)
    revised = bundle(tmp_path / "second", content="revised", revision=revision_request(change))
    second = store.publish(revised, case)
    assert second["version"] == 2 and second["state"] == PENDING and second["receipt"] is None
    assert store.get(token=first["token"])["state"] == "superseded"
    assert store.artifact(first["token"], "report.pdf") == b"first report.pdf"
    with pytest.raises(ReviewConflict, match="no longer"):
        store.decide(first["token"], first["digest"], "approve", "")

    def approve(_):
        return store.decide(second["token"], second["digest"], "approve", "")["receipt"]

    with ThreadPoolExecutor() as executor:
        receipts = list(executor.map(approve, range(4)))
    assert receipts.count(receipts[0]) == 4
    assert receipts[0]["version"] == 2 and receipts[0]["digest"] == second["digest"]
    assert receipts[0]["artifacts"]["report/report.pdf"] == second["manifest"]["report/report.pdf"]
    assert event_kinds(store, case) == ["published", "changes_requested", "action_checked", "published",
                                        "action_checked", "action_checked", "approved", "simulated_delivery"]
    audit = store.action_trace(case)
    assert [(entry["version"], entry["decision"]) for entry in audit] == [
        (1, "block"), (1, "block"), (2, "allow"),
    ]
    assert FinalReviews(db).get(review_id=case)["receipt"] == receipts[0]


def test_changed_or_failing_bundle_cannot_be_published(tmp_path):
    store = FinalReviews(tmp_path / "review.sqlite3")
    folder = bundle(tmp_path / "changed")
    (folder / "report/report.pdf").write_bytes(b"changed")
    with pytest.raises(PilotError, match="differ from the reviewed artifacts"):
        store.publish(folder)
    with pytest.raises(PilotError, match="unresolved checks"):
        store.publish(bundle(tmp_path / "failed", passed=False))


def test_review_page_labels_out_of_coverage_without_a_headline_count(tmp_path):
    store = FinalReviews(tmp_path / "review.sqlite3")
    report = store.publish(bundle(tmp_path / "uncovered", covered=False))
    app = create_server(store, ORIGIN).streamable_http_app()
    with TestClient(app, base_url=ORIGIN) as browser:
        body = browser.get(f"/final-review/{report['token']}")
    assert "Outside snapshot coverage" in body.text
