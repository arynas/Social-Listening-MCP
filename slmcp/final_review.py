"""Human approval of checked S3 reports, with local simulated delivery."""

import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import sys
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import uvicorn
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, PlainTextResponse, Response

from slmcp.actions import check_action
from slmcp.analyst import NewsRequest, PilotError, openai_analyst
from slmcp.context import brief_markdown
from slmcp.orchestrator import failure_message, read_json, start_run
from slmcp.report_jobs import FORMATS, HEADERS, PAGE_HEADERS

PENDING = "awaiting_final_approval"
DECISIONS = {"approve": "approved", "changes": "changes_requested", "reject": "rejected"}
FILES = (*FORMATS, "analysis.json", "report-inputs.json", "check_report.json", "review.json")
LABELS = {
    PENDING: "Awaiting final approval",
    "changes_requested": "Changes requested",
    "rejected": "Rejected",
    "approved": "Approved",
    "superseded": "Superseded",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY, scope TEXT NOT NULL, current INTEGER NOT NULL,
    state TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', receipt TEXT
);
CREATE TABLE IF NOT EXISTS versions (
    token TEXT PRIMARY KEY, review_id TEXT NOT NULL REFERENCES reviews(id),
    version INTEGER NOT NULL, run_id TEXT NOT NULL, digest TEXT NOT NULL,
    manifest TEXT NOT NULL, UNIQUE (review_id, version), UNIQUE (review_id, run_id)
);
CREATE TABLE IF NOT EXISTS artifacts (
    token TEXT NOT NULL REFERENCES versions(token), name TEXT NOT NULL, content BLOB NOT NULL,
    PRIMARY KEY (token, name)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, review_id TEXT NOT NULL REFERENCES reviews(id),
    version INTEGER NOT NULL, kind TEXT NOT NULL, note TEXT NOT NULL, at TEXT NOT NULL
);
"""


class ReviewConflict(PilotError):
    """This action conflicts with the current decision or report version."""


def checked_bundle(folder):
    trace = read_json(folder / "trace.json")
    decision = trace["steps"][-1]
    if (trace["status"] != PENDING or decision.get("status") != PENDING
            or decision.get("stage") != "orchestrator" or decision.get("event") != "decision"):
        raise PilotError("Publish a report that passed S3 checks and review")
    files = {name: (folder / "report" / name).read_bytes() for name in FILES}
    analysis = json.loads(files["analysis.json"])
    if "context" in analysis:
        files["analysis.md"] = (folder / "report" / "analysis.md").read_bytes()
        if files["analysis.md"] != brief_markdown(analysis).encode():
            raise PilotError("The cited brief differs from the reviewed analysis")
    manifest = {f"report/{name}": hashlib.sha256(content).hexdigest() for name, content in files.items()}
    if manifest != decision.get("artifacts"):
        raise PilotError("The report files differ from the reviewed artifacts")
    checks, reviewer, inputs = (json.loads(files[name]) for name in
                                ("check_report.json", "review.json", "report-inputs.json"))
    if (checks["status"] != "pass" or checks["issues"] or reviewer["status"] != "pass"
            or reviewer["findings"] or reviewer["s2_checks"] != checks or not reviewer["analysis_reviewed"]):
        raise PilotError("The report has unresolved checks or reviewer findings")
    query = inputs["query"]
    if (inputs["request"] != trace["request"] or query["snapshot"] != trace["snapshot"]
            or query["timezone"] != trace["timezone"]):
        raise PilotError("The report scope differs from the reviewed run")
    scope = {name: trace[name] for name in ("request", "snapshot", "timezone")}
    return trace, scope, files, manifest


class FinalReviews:
    def __init__(self, db):
        self.db = db
        db.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as conn:
            conn.executescript(SCHEMA)

    def connect(self):
        conn = sqlite3.connect(self.db, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def transaction(self):
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            yield conn

    @staticmethod
    def event(conn, review_id, version, kind, note=""):
        at = datetime.now(UTC).isoformat()
        conn.execute("INSERT INTO events (review_id, version, kind, note, at) VALUES (?, ?, ?, ?, ?)",
                     (review_id, version, kind, note, at))
        return at

    def publish(self, folder, review_id=None):
        trace, scope, files, manifest = checked_bundle(folder)
        encoded_manifest = json.dumps(manifest, sort_keys=True)
        digest = hashlib.sha256(encoded_manifest.encode()).hexdigest()
        with self.transaction() as conn:
            if review_id is None:
                review_id, version = uuid4().hex, 1
                conn.execute("INSERT INTO reviews (id, scope, current, state) VALUES (?, ?, ?, ?)",
                             (review_id, json.dumps(scope, sort_keys=True), version, PENDING))
            else:
                previous = self.lookup(conn, "v.review_id = ? AND v.version = r.current", review_id)
                if previous is None or previous["state"] != "changes_requested":
                    raise ReviewConflict("Only an active change request can receive a replacement")
                if scope != previous["scope"]:
                    raise ReviewConflict("A replacement must use the same report scope and snapshot")
                if trace.get("final_review_revision") != revision_request(previous):
                    raise ReviewConflict("Start a revision from the recorded human change request")
                duplicate = conn.execute(
                    "SELECT 1 FROM versions WHERE review_id = ? AND (run_id = ? OR digest = ?)",
                    (review_id, trace["run_id"], digest),
                ).fetchone()
                if duplicate is not None:
                    raise ReviewConflict("A replacement must be a new checked report")
                version = previous["version"] + 1
                conn.execute("UPDATE reviews SET current = ?, state = ?, note = '' WHERE id = ?",
                             (version, PENDING, review_id))
            token = secrets.token_urlsafe(24)
            conn.execute("INSERT INTO versions VALUES (?, ?, ?, ?, ?, ?)",
                         (token, review_id, version, trace["run_id"], digest, encoded_manifest))
            conn.executemany("INSERT INTO artifacts VALUES (?, ?, ?)",
                             [(token, name, data) for name, data in files.items()])
            self.event(conn, review_id, version, "published")
            return self.lookup(conn, "v.token = ?", token)

    @staticmethod
    def lookup(conn, where, value):
        row = conn.execute(
            """SELECT v.*, r.scope, r.current, r.state, r.note, r.receipt
               FROM versions v JOIN reviews r ON r.id = v.review_id WHERE """ + where, (value,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for field in ("scope", "manifest", "receipt"):
            result[field] = json.loads(result[field]) if result[field] is not None else None
        if result["version"] != result["current"]:
            result.update(state="superseded", note="", receipt=None)
        return result

    def get(self, *, review_id=None, token=None):
        with closing(self.connect()) as conn:
            if token is not None:
                return self.lookup(conn, "v.token = ?", token)
            return self.lookup(conn, "v.review_id = ? AND v.version = r.current", review_id)

    def artifact(self, token, name):
        with closing(self.connect()) as conn:
            result = conn.execute("SELECT content FROM artifacts WHERE token = ? AND name = ?",
                                  (token, name)).fetchone()
        return result["content"] if result else None

    def decide(self, token, digest, action, note):
        note = note.strip()
        if action not in DECISIONS or len(note) > 2000 or (action == "changes" and not note):
            raise ValueError("Select a decision and give a reason when requesting changes")
        with self.transaction() as conn:
            report = self.lookup(conn, "v.token = ?", token)
            if report is None:
                raise LookupError("Review version not found")
            if digest != report["digest"]:
                raise ReviewConflict("The submitted digest differs from the reviewed version")
            if report["state"] == "approved" and action == "approve":
                return report
            if report["state"] != PENDING:
                raise ReviewConflict("This version can no longer be approved or changed")
            review_id, version = report["review_id"], report["version"]
            state = DECISIONS[action]
            self.event(conn, review_id, version, state, note)
            receipt = None
            if state == "approved":
                action = {"kind": "simulate_report_delivery", "target": "local_receipt",
                          "version": version, "digest": digest}
                decision = check_action(action, approved={"version": version, "digest": report["digest"]})
                if decision["decision"] != "allow":
                    raise ReviewConflict(decision["reason"])
                at = self.event(conn, review_id, version, "simulated_delivery", decision["reason"])
                artifacts = {path: digest for path, digest in report["manifest"].items()
                             if path in ("report/report.pdf", "report/report.pptx", "report/analysis.md")}
                receipt = json.dumps({"mode": "simulation", "at": at, "version": version,
                                      "digest": digest, "artifacts": artifacts})
            conn.execute("UPDATE reviews SET state = ?, note = ?, receipt = ? WHERE id = ?",
                         (state, note, receipt, review_id))
            return self.lookup(conn, "v.token = ?", token)


def status(report, browser_url=None):
    result = {key: report[key] for key in
              ("review_id", "version", "digest", "scope", "state", "note", "receipt")}
    if browser_url is not None:
        result["review_url"] = f"{browser_url}/final-review/{report['token']}"
    return result


def context_section(analysis, brief_url):
    context = analysis.get("context")
    if context is None:
        return ""
    evidence = context["evidence"]
    statements = []
    for item in (*context["findings"], *evidence["notices"]):
        refs = ", ".join(f'<a href="#{ref}">{ref}</a>' for ref in item["evidence"])
        citations = f" ({refs})" if refs else ""
        statements.append(f"<li>{escape(item['text'])}{citations}</li>")
    sources = []
    for ref, doc in evidence["sources"].items():
        label = escape(f"{doc['publisher']} · {doc['title']} · {doc['date']}")
        url = doc["url"]
        title = f'<a href="{escape(url)}" rel="noreferrer">{label}</a>' if url else label
        if doc["synthetic"]:
            title += " (synthetic)"
        quote = escape(f"{doc['section']} · {doc['excerpt']}")
        sources.append(f'<li id="{ref}">{ref} · {title}<br>{quote}</li>')
    return (f'<h2>Context and source limitations</h2><p><a href="{brief_url}">Download cited brief</a></p>'
            f"<ul>{''.join(statements)}</ul><h3>Source passages</h3><ul>{''.join(sources)}</ul>")


def review_page(store, report):
    token, state = report["token"], report["state"]
    scope = report["scope"]
    request = scope["request"]
    analysis = json.loads(store.artifact(token, "analysis.json"))
    reviewer = json.loads(store.artifact(token, "review.json"))
    checks = reviewer["s2_checks"]
    expected = checks["expected"]
    volume = f"{expected['total']:,} headlines" if expected["data_available"] else "Outside snapshot coverage"
    findings = "".join(
        f"<li>{escape(item['text'])} <small>({escape(', '.join(item['evidence']))})</small></li>"
        for item in analysis["findings"]
    ) or "<li>No qualitative findings were included.</li>"
    links = (f'<a href="/final-review/{token}/report.pptx">Download PPTX</a> · '
             f'<a href="/final-review/{token}/report.pdf">View PDF</a>')
    hashes = "".join(f"<li>{name}<br><code>{digest}</code></li>"
                     for name, digest in report["manifest"].items())
    note = f"<p><strong>Decision note</strong> {escape(report['note'])}</p>" if report["note"] else ""
    receipt = "<p>Simulated delivery recorded for this version.</p>" if report["receipt"] else ""
    form = ""
    if state == PENDING:
        form = f"""
        <h2>Final decision</h2>
        <form method="post">
        <input type="hidden" name="digest" value="{report['digest']}">
        <label for="note">Decision note (required for changes)</label><br>
        <textarea id="note" name="note" rows="4" cols="60" maxlength="2000"></textarea><br>
        <button name="action" value="approve">Approve report</button>
        <button name="action" value="changes">Request changes</button>
        <button name="action" value="reject">Reject report</button>
        </form>"""
    return HTMLResponse(f"""<!doctype html><html lang="en"><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Final report review</title><main>
        <h1>{escape(request['keyword'])} · Final report review</h1>
        <p>{escape(request['start'])} through {escape(request['end'])} · {escape(scope['timezone'])}
        · Snapshot {escape(scope['snapshot'])}</p>
        <h2>Version {report['version']} · {LABELS[state]}</h2>
        <p>Version digest <code>{report['digest']}</code></p>{note}{receipt}
        <h2>Reviewed report files</h2><p>{links}</p>
        <h2>Analysis</h2><ul>{findings}</ul>
        {context_section(analysis, f"/final-review/{token}/analysis.md")}
        <h2>Reviewer summary</h2>
        <p>Reviewer {escape(reviewer['status'])}, {len(reviewer['findings'])} open findings.
        Figures, links and layout {escape(checks['status'])}, {len(checks['issues'])} issues.</p>
        <p>{expected['slides']} slides · {volume} ·
        {expected['headline_links']} checked headline links.</p>
        <details><summary>Reviewed file hashes</summary><ul>{hashes}</ul></details>
        {form}</main></html>""", headers=PAGE_HEADERS)


def create_server(store, browser_url):
    origin = urlsplit(browser_url)
    if origin.scheme not in ("http", "https") or not origin.netloc or browser_url != (
        f"{origin.scheme}://{origin.netloc}"
    ):
        raise ValueError("The browser URL must be an HTTP(S) origin")
    server = MCPServer("Analyst final report review")

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)
    def get_analyst_final_review(review_id: str) -> str:
        """Look up the final report status and the exact version's human review link. Read-only."""
        report = store.get(review_id=review_id)
        if report is None:
            return "Final report review not found. Ask the analyst for the case ID."
        result = status(report, browser_url)
        result.pop("note")
        result["instructions"] = "Share the clickable review link. Use the page for decisions."
        return json.dumps(result, ensure_ascii=False)

    @server.custom_route("/final-review/{token}", methods=["GET", "POST"], include_in_schema=False)
    async def review(request):
        token = request.path_params["token"]
        try:
            if request.method == "POST":
                if request.headers.get("origin") != browser_url:
                    return PlainTextResponse("Wrong approval origin", status_code=403, headers=HEADERS)
                content_type = request.headers.get("content-type", "").partition(";")[0]
                if content_type != "application/x-www-form-urlencoded":
                    raise ValueError("Use the report decision form")
                body = bytearray()
                async for chunk in request.stream():
                    if len(body) + len(chunk) > 25000:
                        raise ValueError("Decision form is too large")
                    body.extend(chunk)
                values = parse_qs(body.decode(), keep_blank_values=True, max_num_fields=3)
                fields = [values.get(name, [""]) for name in ("digest", "action", "note")]
                if any(len(field) != 1 for field in fields):
                    raise ValueError("Submit one value per decision field")
                report = await run_in_threadpool(store.decide, token, *(field[0] for field in fields))
            else:
                report = await run_in_threadpool(store.get, token=token)
                if report is None:
                    raise LookupError("Review version not found")
            return await run_in_threadpool(review_page, store, report)
        except ReviewConflict as error:
            return PlainTextResponse(str(error), status_code=409, headers=HEADERS)
        except LookupError as error:
            return PlainTextResponse(str(error), status_code=404, headers=HEADERS)
        except ValueError as error:
            return PlainTextResponse(str(error), status_code=400, headers=HEADERS)

    @server.custom_route("/final-review/{token}/{name}", methods=["GET"], include_in_schema=False)
    async def artifact(request):
        token, name = (request.path_params[key] for key in ("token", "name"))
        media_type = FORMATS.get(name) if name != "analysis.md" else "text/plain"
        content = await run_in_threadpool(store.artifact, token, name) if media_type else None
        if content is None:
            return PlainTextResponse("Reviewed artifact not found", status_code=404, headers=HEADERS)
        disposition = "inline" if name.endswith(".pdf") else "attachment"
        return Response(content, media_type=media_type,
                        headers={**HEADERS, "Content-Disposition": f'{disposition}; filename="{name}"'})

    return server


def revision_request(report):
    return {key: report[key] for key in ("review_id", "version", "note")}


def start_revision(store, review_id, folder, mcp_url, kit, data_dir, chat, model):
    report = store.get(review_id=review_id)
    if report is None or report["state"] != "changes_requested":
        raise ReviewConflict("A human change request is required to start a revision")
    previous = json.loads(store.artifact(report["token"], "analysis.json"))
    return start_run(folder, NewsRequest(**report["scope"]["request"]), mcp_url, kit, data_dir, chat, model,
                     final_review_revision=revision_request(report),
                     frozen_context=previous.get("context", {}).get("evidence"),
                     snapshot=report["scope"]["snapshot"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("publish", help="register the exact checked report for final review")
    show = commands.add_parser("status", help="show approval state or the current change request")
    revise = commands.add_parser("start-revision", help="start an outline from the human change request")
    serve = commands.add_parser("serve", help="serve read-only MCP and the human review page")
    for command in (publish, show, revise, serve):
        command.add_argument("--db", required=True, type=Path)
    publish.add_argument("--run", required=True, type=Path)
    publish.add_argument("--review-id")
    show.add_argument("--review-id", required=True)
    revise.add_argument("--review-id", required=True)
    revise.add_argument("--run", required=True, type=Path)
    revise.add_argument("--data-dir", required=True, type=Path)
    revise.add_argument("--kit", default="llm-report-kit")
    revise.add_argument("--mcp-url", default="http://127.0.0.1:8769/mcp")
    revise.add_argument("--analyst-base", required=True)
    revise.add_argument("--analyst-model", required=True)
    revise.add_argument("--analyst-key-env")
    revise.add_argument("--thinking-off", action="store_true")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8771, type=int)
    serve.add_argument("--browser-url", default="http://127.0.0.1:8771")
    args = parser.parse_args(argv)
    try:
        store = FinalReviews(args.db)
        if args.command == "serve":
            server = create_server(store, args.browser_url)
            app = server.streamable_http_app(stateless_http=True, json_response=True, host=args.host)
            uvicorn.run(app, host=args.host, port=args.port, access_log=False)
            return 0
        if args.command == "start-revision":
            key = os.environ.get(args.analyst_key_env) if args.analyst_key_env else None
            if args.analyst_key_env and not key:
                raise PilotError(f"Set the analyst credential in {args.analyst_key_env}")
            chat = openai_analyst(args.analyst_base, args.analyst_model, key, thinking_off=args.thinking_off)
            trace = start_revision(store, args.review_id, args.run.resolve(), args.mcp_url, args.kit,
                                   args.data_dir.resolve(), chat, args.analyst_model)
            print(json.dumps({key: trace[key] for key in ("run_id", "status", "final_review_revision")}))
            return 0
        report = (store.publish(args.run, args.review_id) if args.command == "publish"
                  else store.get(review_id=args.review_id))
        if report is None:
            raise PilotError("Final report review not found")
        print(json.dumps(status(report), ensure_ascii=False))
    except (KeyError, OSError, ValueError, PilotError, IndexError) as error:
        print(f"s3 final review: {failure_message(error)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
