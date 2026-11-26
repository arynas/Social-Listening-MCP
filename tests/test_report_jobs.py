import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path
from threading import Event

from mcp import Client
from starlette.testclient import TestClient

from slmcp.config import load_config
from slmcp.report_jobs import ReportJobs
from slmcp.server import build_server

CONFIG = Path(__file__).parents[1] / "config.example.yaml"
ORIGIN = "http://127.0.0.1:8769"
REQUEST = {"keyword": "banjir", "dates": {"start": "2026-11-01", "end": "2026-11-09"}}


def call(server, name, args):
    async def run():
        async with Client(server) as client:
            result = await client.call_tool(name, args)
            assert not result.is_error
            return json.loads(result.content[0].text)

    return asyncio.run(run())


def write_outputs(path, passed):
    (path / "report.pptx").write_bytes(b"pptx output")
    (path / "report.pdf").write_bytes(b"pdf output")
    (path / "check_report.json").write_text(json.dumps({"status": "pass" if passed else "fail"}))


def wait_for_status(jobs, report_id, status):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = jobs.status(report_id)
        if result["status"] == status:
            return result
        time.sleep(.01)
    raise AssertionError(result)


def test_outline_waits_for_browser_approval_and_queues_only_once(tmp_path):
    rendering, finish = Event(), Event()
    requests = []

    def render(inputs, path):
        requests.append(inputs)
        rendering.set()
        assert finish.wait(4)
        write_outputs(path, passed=True)

    jobs = ReportJobs(tmp_path, ORIGIN, render)
    config = replace(load_config(CONFIG), today="2026-11-10")
    server = build_server(config, report_jobs=jobs, report_snapshot="synthetic")
    try:
        with TestClient(server.streamable_http_app(), base_url=ORIGIN) as browser:
            outline = call(server, "build_report", REQUEST)
            report_id, approval = outline["report_id"], outline["approval_url"]
            assert outline["status"] == "awaiting_approval" and outline["pages"] == 4
            review = browser.get(approval)
            assert "Approve outline and render" in review.text
            assert review.headers["Referrer-Policy"] == "same-origin"
            assert browser.post(approval, headers={"Origin": "https://example.invalid"}).status_code == 403
            assert browser.post(approval, headers={"Origin": "null"}).status_code == 403
            assert requests == []
            not_ready = f"/reports/{report_id}/report.pdf"
            assert browser.get(not_ready).status_code == 404

            approved = browser.post(approval, headers={"Origin": ORIGIN})
            assert approved.status_code == 202 and "Sedang diproses" in approved.text
            assert rendering.wait(2)
            assert browser.post(approval, headers={"Origin": ORIGIN}).status_code == 200
            assert "downloads" not in call(server, "report_status", {"report_id": report_id})
            assert browser.get(not_ready).status_code == 404
            finish.set()

            wait_for_status(jobs, report_id, "ready")
            ready = call(server, "report_status", {"report_id": report_id})
            assert len(requests) == 1
            for ext, url in ready["downloads"].items():
                delivered = browser.get(url)
                assert delivered.content == f"{ext} output".encode()
                assert "attachment" in delivered.headers["Content-Disposition"]
            assert browser.get(f"/reports/{report_id}/check_report.json").status_code == 404
    finally:
        finish.set()
        jobs.executor.shutdown()


def test_rejected_render_can_retry_and_ready_job_survives_restart(tmp_path):
    attempts = []

    def render(inputs, directory):
        attempts.append(inputs)
        write_outputs(directory, passed=len(attempts) > 1)

    jobs = ReportJobs(tmp_path, ORIGIN, render)
    config = replace(load_config(CONFIG), today="2026-11-10")
    server = build_server(config, report_jobs=jobs, report_snapshot="synthetic")
    try:
        with TestClient(server.streamable_http_app(), base_url=ORIGIN) as browser:
            request = dict(REQUEST, dates={"start": "2026-08-18", "end": "2026-08-31"})
            outline = call(server, "build_report", request)
            assert outline["pages"] == 1 and "summary" not in outline
            report_id, approval = outline["report_id"], outline["approval_url"]
            assert browser.post(approval, headers={"Origin": ORIGIN}).status_code == 202
            failed = wait_for_status(jobs, report_id, "failed")
            assert "downloads" not in failed
            assert "Retry rendering" in browser.get(approval).text
            assert browser.post(approval, headers={"Origin": ORIGIN}).status_code == 200
            assert len(attempts) == 1
            assert browser.post(approval + "?retry=1", headers={"Origin": ORIGIN}).status_code == 202
            ready = wait_for_status(jobs, report_id, "ready")
            assert len(attempts) == 2
            assert attempts[1]["data_available"] is False and attempts[1]["facts"] == {}
    finally:
        jobs.executor.shutdown()
    restarted = ReportJobs(tmp_path, ORIGIN, render)
    try:
        assert ready == restarted.status(report_id)
    finally:
        restarted.executor.shutdown()
