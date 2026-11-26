"""Approval, background execution and checked report downloads for Streamable HTTP."""

import json
import logging
import secrets
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from starlette.responses import FileResponse, HTMLResponse, PlainTextResponse

log = logging.getLogger(__name__)
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}
PAGE_HEADERS = {
    **HEADERS,
    # Browsers send Origin: null on form POSTs from a no-referrer page.
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'none'; form-action 'self'; frame-ancestors 'none'",
}
FORMATS = {
    "report.pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "report.pdf": "application/pdf",
}


@dataclass(frozen=True)
class ReportRenderer:
    script: Path
    python: str
    source_name: str
    source_url: str

    def __call__(self, inputs, directory):
        source = directory / "report-inputs.json"
        source.write_text(json.dumps(inputs, ensure_ascii=False), encoding="utf-8")
        subprocess.run(
            [
                self.python,
                str(self.script),
                "build-report",
                "--report-inputs",
                str(source),
                "--name",
                "report",
                "--output-dir",
                str(directory),
                "--source-name",
                self.source_name,
                "--source-url",
                self.source_url,
            ],
            cwd=self.script.parent,
            capture_output=True,
            timeout=180,
            check=True,
        )


class ReportJobs:
    """One renderer per service. Status and completed files survive a process restart."""

    def __init__(self, root, base_url, render):
        self.root = Path(root).resolve()
        parsed = urlsplit(base_url)
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path.rstrip("/")
            or parsed.query
            or parsed.fragment
            or (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))
        ):
            raise ValueError("report base URL must be an HTTPS origin, or an HTTP loopback origin")
        self.origin = f"{parsed.scheme}://{parsed.netloc}"
        self.render = render
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "reports.sqlite3"
        with closing(self.connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, approval TEXT UNIQUE NOT NULL, "
                "inputs TEXT NOT NULL, state TEXT NOT NULL)"
            )
            db.execute("UPDATE reports SET state = 'failed' WHERE state IN ('queued', 'rendering')")
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="report-render")

    def connect(self):
        db = sqlite3.connect(self.database)
        db.row_factory = sqlite3.Row
        return db

    def create(self, inputs):
        report_id, approval = (secrets.token_urlsafe(24) for _ in range(2))
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT INTO reports VALUES (?, ?, ?, 'awaiting_approval')",
                (report_id, approval, json.dumps(inputs, ensure_ascii=False)),
            )
        result = {
            "report_id": report_id,
            "status": "awaiting_approval",
            "query": inputs["query"],
            "pages": len(inputs["outline"]),
            "outline": [s["title"] for s in inputs["outline"]],
            "warnings": inputs["warnings"],
            "approval_url": f"{self.origin}/reports/approve/{approval}",
        }
        if inputs["data_available"]:
            result["summary"] = {"total": inputs["facts"]["total"], "peak": inputs["facts"]["peak"]}
        return result

    def find(self, *, report_id=None, approval=None):
        with closing(self.connect()) as db:
            return db.execute(
                "SELECT * FROM reports WHERE id = ? OR approval = ?", (report_id, approval)
            ).fetchone()

    def status(self, report_id):
        job = self.find(report_id=report_id)
        if job is None:
            return {"error": "Report not found"}
        result = {"report_id": report_id, "status": job["state"]}
        if job["state"] == "ready":
            result["downloads"] = {
                ext: f"{self.origin}/reports/{report_id}/report.{ext}" for ext in ("pptx", "pdf")
            }
        if job["state"] == "failed":
            result["message"] = "Rendering or verification failed. Use the original approval page to retry."
        return result

    def approve(self, job, *, retry=False):
        expected = "failed" if retry else "awaiting_approval"
        with closing(self.connect()) as db, db:
            queued = db.execute(
                "UPDATE reports SET state = 'queued' WHERE id = ? AND state = ?",
                (job["id"], expected),
            ).rowcount
        if queued:
            try:
                self.executor.submit(self.run, job["id"], json.loads(job["inputs"]))
            except RuntimeError:
                self.set_state(job["id"], "failed")
                return False
        return bool(queued)

    def set_state(self, report_id, state):
        with closing(self.connect()) as db, db:
            db.execute("UPDATE reports SET state = ? WHERE id = ?", (state, report_id))

    def run(self, report_id, inputs):
        try:
            self.set_state(report_id, "rendering")
            with TemporaryDirectory(prefix="work-", dir=self.root) as staging:
                directory = Path(staging)
                self.render(inputs, directory)
                receipt = json.loads((directory / "check_report.json").read_text(encoding="utf-8"))
                if receipt.get("status") != "pass" or any(
                    (directory / name).stat().st_size == 0 for name in FORMATS
                ):
                    raise ValueError("report exports did not pass checks")
                destination = self.root / report_id
                destination.mkdir(exist_ok=True)
                for name in (*FORMATS, "check_report.json"):
                    (directory / name).replace(destination / name)
            self.set_state(report_id, "ready")
        except Exception:
            log.exception("Report rendering failed")
            self.set_state(report_id, "failed")


def approval_page(job, jobs, *, queued=False):
    inputs = json.loads(job["inputs"])
    query = inputs["query"]
    state = job["state"]
    title = escape(query["keyword"])
    dates = f"{escape(query['start_date'])} to {escape(query['end_date'])} (WIB)"
    outline = "".join(f"<li>{escape(s['title'])}</li>" for s in inputs["outline"])
    notes = "".join(f"<li>{escape(line)}</li>" for line in inputs["warnings"])
    summary = ""
    if inputs["data_available"]:
        facts = inputs["facts"]
        summary = f"<p>{facts['total']:,} headlines.</p>"
        if facts["peak"]:
            peak = facts["peak"]
            summary += f"<p>Peak: {peak['count']:,} headlines on {escape(peak['at'])}.</p>"
    if queued or state in {"queued", "rendering"}:
        action = '<p>Sedang diproses. <a href="?">Check status</a>.</p>'
    elif state == "ready":
        links = jobs.status(job["id"])["downloads"]
        action = "".join(
            f'<p><a href="{escape(url)}">Download {ext.upper()}</a></p>' for ext, url in links.items()
        )
    else:
        label = "Retry rendering" if state == "failed" else "Approve outline and render"
        form_action = "?retry=1" if state == "failed" else "?"
        action = f'<form method="post" action="{form_action}"><button type="submit">{label}</button></form>'
        if state == "failed":
            action = "<p>Rendering or verification failed.</p>" + action
    return HTMLResponse(
        f'<!doctype html><html lang="en"><meta charset="utf-8"><title>Report approval</title>'
        f"<main><h1>{title}</h1><p>{dates}</p>{summary}<h2>Report outline</h2><ol>{outline}</ol>"
        f"<h2>Data notes</h2><ul>{notes}</ul>{action}</main></html>",
        status_code=202 if queued else 200,
        headers=PAGE_HEADERS,
    )


def add_report_routes(server, jobs):
    @server.custom_route("/reports/approve/{approval}", methods=["GET", "POST"], include_in_schema=False)
    async def approve(request):
        if request.method == "POST" and request.headers.get("origin") != jobs.origin:
            return PlainTextResponse("Wrong approval origin", status_code=403, headers=HEADERS)
        job = jobs.find(approval=request.path_params["approval"])
        if job is None:
            return PlainTextResponse("Report not found", status_code=404, headers=HEADERS)
        retry = request.query_params.get("retry") == "1"
        queued = request.method == "POST" and jobs.approve(job, retry=retry)
        return approval_page(jobs.find(report_id=job["id"]), jobs, queued=queued)

    @server.custom_route("/reports/{report_id}/{filename}", methods=["GET"], include_in_schema=False)
    async def download(request):
        filename = request.path_params["filename"]
        job = jobs.find(report_id=request.path_params["report_id"])
        if filename not in FORMATS or job is None or job["state"] != "ready":
            return PlainTextResponse("Report file not found", status_code=404, headers=HEADERS)
        path = jobs.root / job["id"] / filename
        if not path.is_file():
            return PlainTextResponse("Report file not found", status_code=404, headers=HEADERS)
        return FileResponse(path, media_type=FORMATS[filename], filename=filename, headers=HEADERS)
