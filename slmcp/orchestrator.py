"""S3-1 local Analyst Agent pilot for the frozen public news snapshot."""

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from slmcp.analyst import NewsRequest, PilotError, collect, make_brief, openai_analyst, public_facts

SNAPSHOT = "data-2026-10-v2"
PILOT_TZ = "Asia/Jakarta"
APPROVED_FILES = ("outline/outline.md", "outline/report-inputs.json", "analysis.json")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, payload):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        pending = Path(stream.name)
        try:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.close()
            pending.replace(path)
        finally:
            pending.unlink(missing_ok=True)


def digests(folder, names):
    result = {}
    for name in names:
        with (folder / name).open("rb") as stream:
            result[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def failure_message(error):
    if isinstance(error, BaseExceptionGroup):
        return failure_message(error.exceptions[0])
    return str(error) or type(error).__name__


class Run:
    def __init__(self, folder, trace):
        self.folder = folder
        self.trace = trace

    @classmethod
    def create(cls, folder, request):
        folder.mkdir(parents=True, exist_ok=False)
        run = cls(folder, {"schema_version": 1, "run_id": uuid4().hex, "request": asdict(request),
                           "snapshot": SNAPSHOT, "timezone": PILOT_TZ, "status": "running", "steps": []})
        run.add("orchestrator", "input", request=asdict(request))
        return run

    def add(self, stage, event, **detail):
        step = {"stage": stage, "event": event, "time": datetime.now(UTC).isoformat(), **detail}
        self.trace["steps"].append(step)
        write_json(self.folder / "trace.json", self.trace)

    @contextmanager
    def stage(self, name):
        start = time.monotonic()
        self.add(name, "started")
        try:
            yield
        except Exception as error:
            self.trace["status"] = "failed"
            self.add(name, "failed", error=failure_message(error))
            raise
        else:
            self.add(name, "completed", duration_ms=round((time.monotonic() - start) * 1000))

    def transition(self, status, **detail):
        self.trace["status"] = status
        self.add("orchestrator", "decision", status=status, **detail)


def kit_command(kit, *args):
    completed = subprocess.run([kit, *map(str, args)],
                               capture_output=True, text=True, timeout=180, check=False)
    if completed.returncode:
        details = (completed.stderr or completed.stdout).strip()
        raise PilotError(f"S2 report kit {args[0]} failed: {details}")


def compare_outline(inputs, evidence, request):
    query = inputs["query"]
    if (inputs["request"] != asdict(request) or query["snapshot"] != SNAPSHOT
            or query["timezone"] != PILOT_TZ):
        raise PilotError("The report outline did not use the pilot request and snapshot")
    projection = {"data_available": inputs["data_available"], "facts": public_facts(inputs["facts"]),
                  "warnings": inputs["warnings"]}
    if projection != evidence:
        mismatches = ", ".join(key for key in projection if projection[key] != evidence[key])
        raise PilotError(f"MCP evidence and the S2 report outline disagree on {mismatches}")


def start_run(folder, request, mcp_url, kit, data_dir, chat, model):
    run = Run.create(folder, request)
    with run.stage("data"):
        evidence = asyncio.run(collect(mcp_url, request,
                                       lambda event, **info: run.add("data", event, **info)))
        write_json(folder / "evidence.json", evidence)
        run.add("data", "artifact", path="evidence.json", sources=["news_recap", "entity_trend"])
    with run.stage("analyst"):
        brief = make_brief(evidence, chat, model)
        write_json(folder / "analysis.json", brief)
        run.add("analyst", "artifact", path="analysis.json", status=brief["status"], model=brief["model"],
                attempts=brief["attempts"])
    with run.stage("outline"):
        kit_command(kit, "outline", "--data-dir", data_dir, "--keyword", request.keyword,
                    "--start", request.start, "--end", request.end, "--out", folder / "outline")
        compare_outline(read_json(folder / "outline/report-inputs.json"), evidence, request)
        run.add("outline", "check", status="pass", against="MCP evidence and verified snapshot")
    run.trace["outline_digests"] = digests(folder, APPROVED_FILES)
    run.transition("awaiting_outline_approval", artifacts=list(APPROVED_FILES))
    return run.trace


def review(folder, inputs):
    report = folder / "report"
    checks = read_json(report / "check_report.json")
    facts, available = inputs["facts"], inputs["data_available"]
    expected = {"slides": 4 if available else 1, "data_available": available, "total": facts.get("total"),
                "peak": facts.get("peak"), "daily_chart_points": len(facts.get("daily_trend", [])),
                "headline_links": len({story["url"] for story in facts.get("top_stories", [])[:3]})}
    if checks.get("status") != "pass" or checks.get("issues") != [] or checks.get("expected") != expected:
        raise PilotError("The S2 checker did not confirm the reviewed report facts")
    if read_json(report / "report-inputs.json") != inputs:
        raise PilotError("The rendered report inputs differ from the approved outline")
    for name, signature in (("report.pptx", b"PK"), ("report.pdf", b"%PDF-")):
        with (report / name).open("rb") as stream:
            if stream.read(len(signature)) != signature:
                raise PilotError(f"Missing or invalid generated {name}")
    if read_json(report / "analysis.json") != read_json(folder / "analysis.json"):
        raise PilotError("The report brief differs from the reviewed analysis")
    return expected


def approve_outline(folder, kit, data_dir):
    if not (folder / "trace.json").is_file():
        raise PilotError("The run has no trace.json")
    with (folder / ".approval.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise PilotError("The outline is already being processed") from error
        run = Run(folder, read_json(folder / "trace.json"))
        status = run.trace["status"]
        if status == "awaiting_final_approval":
            recorded = run.trace["steps"][-1]["artifacts"]
            if digests(folder, recorded) != recorded:
                raise PilotError("The checked draft artifacts changed after review")
            return run.trace
        if status != "awaiting_outline_approval":
            raise PilotError(f"This run cannot approve an outline in state {status}")
        inputs_path = folder / "outline/report-inputs.json"
        if digests(folder, APPROVED_FILES) != run.trace["outline_digests"]:
            raise PilotError("The approved inputs or analysis changed. Start a new run.")
        run.transition("rendering", approval="outline", approved=run.trace["outline_digests"])
        with run.stage("report"):
            kit_command(kit, "build", "--data-dir", data_dir, "--inputs", inputs_path,
                        "--out", folder / "report")
            if digests(folder, APPROVED_FILES) != run.trace["outline_digests"]:
                raise PilotError("The approved outline or analysis changed during rendering")
            write_json(folder / "report/analysis.json", read_json(folder / "analysis.json"))
            run.add("report", "artifact", path="report", brief="report/analysis.json")
        with run.stage("reviewer"):
            result = review(folder, read_json(inputs_path))
            run.add("reviewer", "check", status="pass", findings=0, expected=result)
        artifacts = digests(folder, ("report/report.pptx", "report/report.pdf", "report/check_report.json",
                                     "report/analysis.json", "report/report-inputs.json"))
        run.transition("awaiting_final_approval", artifacts=artifacts)
        return run.trace


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="collect evidence and prepare an outline for approval")
    approve = commands.add_parser("approve-outline", help="approve the outline and build a checked draft")
    for command in (start, approve):
        command.add_argument("--run", type=Path, required=True)
        command.add_argument("--kit", default="llm-report-kit", help="path to the S2 report kit executable")
        command.add_argument("--data-dir", type=Path, required=True, help="verified S2 snapshot directory")
    start.add_argument("--keyword", required=True)
    start.add_argument("--start", required=True)
    start.add_argument("--end", required=True)
    start.add_argument("--mcp-url", default="http://127.0.0.1:8769/mcp")
    start.add_argument("--analyst-base", required=True, help="OpenAI-compatible endpoint base")
    start.add_argument("--analyst-model", required=True)
    start.add_argument("--analyst-key-env", help="environment variable containing the analyst key")
    start.add_argument("--thinking-off", action="store_true", help="send the Qwen chat template option")
    args = parser.parse_args(argv)
    folder, data_dir = args.run.resolve(), args.data_dir.resolve()
    try:
        if args.command == "start":
            key = os.environ.get(args.analyst_key_env) if args.analyst_key_env else None
            if args.analyst_key_env and not key:
                raise PilotError(f"Set the analyst credential in {args.analyst_key_env}")
            chat = openai_analyst(args.analyst_base, args.analyst_model, key, thinking_off=args.thinking_off)
            request = NewsRequest(args.keyword.strip(), args.start, args.end)
            trace = start_run(folder, request, args.mcp_url, args.kit, data_dir, chat, args.analyst_model)
        else:
            trace = approve_outline(folder, args.kit, data_dir)
    except Exception as error:
        print(f"s3 pilot: {failure_message(error)}", file=sys.stderr)
        return 1
    print(json.dumps({"run_id": trace["run_id"], "status": trace["status"],
                      "trace": str(folder / "trace.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
