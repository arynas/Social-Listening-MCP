"""S3-1 local Analyst Agent pilot for the frozen public news snapshot."""

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from uuid import uuid4

from slmcp import __version__
from slmcp.analyst import NewsRequest, PilotError, collect, make_brief, openai_analyst, public_facts
from slmcp.context import brief_markdown, collect_context
from slmcp.reviewer import review_candidate

SNAPSHOT = "data-2026-10-v2"
PILOT_TZ = "Asia/Jakarta"
APPROVED_FILES = ("outline/outline.md", "outline/report-inputs.json", "analysis.json")
REPORT_FILES = ("report.pptx", "report.pdf", "analysis.json", "report-inputs.json", "check_report.json",
                "review.json")
MAX_REVISIONS = 2


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
    def create(cls, folder, request, *, snapshot=SNAPSHOT):
        folder.mkdir(parents=True, exist_ok=False)
        run = cls(folder, {"schema_version": 2, "run_id": uuid4().hex, "request": asdict(request),
                           "snapshot": snapshot, "timezone": PILOT_TZ, "status": "running", "steps": [],
                           "configuration": {"slmcp_version": __version__, "max_revisions": MAX_REVISIONS}})
        run.add("orchestrator", "input", request=asdict(request))
        return run

    def add(self, stage, event, **detail):
        step = {"stage": stage, "event": event, "time": datetime.now(UTC).isoformat(), **detail}
        if self.trace["schema_version"] >= 2:
            step["seq"] = len(self.trace["steps"]) + 1
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


def kit_command(kit, *args, accepted=(0,)):
    command = [kit] if isinstance(kit, (str, os.PathLike)) else kit
    completed = subprocess.run([*command, *map(str, args)],
                               capture_output=True, text=True, timeout=180, check=False)
    if completed.returncode not in accepted:
        details = (completed.stderr or completed.stdout).strip()
        raise PilotError(f"S2 report kit {args[0]} failed: {details}")
    return completed.returncode


def compare_outline(inputs, evidence, request, *, snapshot=SNAPSHOT):
    query = inputs["query"]
    if (inputs["request"] != asdict(request) or query["snapshot"] != snapshot
            or query["timezone"] != PILOT_TZ):
        raise PilotError("The report outline did not use the requested report scope and snapshot")
    projection = {"data_available": inputs["data_available"], "facts": public_facts(inputs["facts"]),
                  "warnings": inputs["warnings"]}
    if projection != evidence:
        mismatches = ", ".join(key for key in projection if projection[key] != evidence[key])
        raise PilotError(f"MCP evidence and the S2 report outline disagree on {mismatches}")


def start_run(folder, request, mcp_url, kit, data_dir, chat, model, *,
              final_review_revision=None, web_url=None, kb_url=None, frozen_context=None, snapshot=SNAPSHOT):
    run = Run.create(folder, request, snapshot=snapshot)
    if final_review_revision:
        run.trace["final_review_revision"] = final_review_revision
        run.add("analyst", "human_change_request", **final_review_revision)
    with run.stage("data"):
        evidence = asyncio.run(collect(mcp_url, request,
                                       lambda event, **info: run.add("data", event, **info)))
        write_json(folder / "evidence.json", evidence)
        run.add("data", "artifact", path="evidence.json", sources=["news_recap", "entity_trend"],
                hashes=digests(folder, ("evidence.json",)))
    if evidence["data_available"] and (web_url or kb_url or frozen_context):
        with run.stage("context"):
            if frozen_context is None:
                context = asyncio.run(collect_context(
                    request, web_url, kb_url, lambda event, **info: run.add("context", event, **info)
                ))
            else:
                context = frozen_context
                run.add("context", "reused", source="previous reviewed version")
            write_json(folder / "context.json", context)
            run.add("context", "artifact", path="context.json", services=context["services"],
                    hashes=digests(folder, ("context.json",)))
            evidence["context"] = context
    with run.stage("analyst"):
        change_note = final_review_revision["note"] if final_review_revision else None
        brief = make_brief(evidence, chat, model, change_note=change_note)
        write_json(folder / "analysis.json", brief)
        run.add("analyst", "artifact", path="analysis.json", status=brief["status"], model=brief["model"],
                attempts=brief["attempts"], hashes=digests(folder, ("analysis.json",)))
    with run.stage("outline"):
        kit_command(kit, "outline", "--data-dir", data_dir, "--keyword", request.keyword,
                    "--start", request.start, "--end", request.end, "--out", folder / "outline")
        facts = {key: evidence[key] for key in ("data_available", "facts", "warnings")}
        inputs = read_json(folder / "outline/report-inputs.json")
        compare_outline(inputs, facts, request, snapshot=snapshot)
        run.add("outline", "check", status="pass", against="MCP evidence and verified snapshot")
    approved = (*APPROVED_FILES, "context.json") if "context" in evidence else APPROVED_FILES
    run.trace["outline_digests"] = digests(folder, approved)
    run.transition("awaiting_outline_approval", artifacts=list(approved))
    return run.trace


def require_approved_inputs(run):
    recorded = run.trace["outline_digests"]
    if digests(run.folder, recorded) != recorded:
        raise PilotError("The approved inputs or analysis changed. Start a new run.")


def render_draft(run_kit, data_dir, inputs_path, candidate, brief):
    run_kit("build", "--data-dir", data_dir, "--inputs", inputs_path, "--out", candidate)
    if brief.is_file():
        shutil.copyfile(brief, candidate / "analysis.json")


def review_and_revise(run, run_kit, data_dir, inputs_path, inputs, evidence, judge, model):
    drafts = run.folder / "drafts"
    drafts.mkdir()
    candidate = drafts / "01"
    with run.stage("report"):
        render_draft(run_kit, data_dir, inputs_path, candidate, run.folder / "analysis.json")
        run.add("report", "artifact", path="drafts/01")
    for number in range(1, MAX_REVISIONS + 2):
        with run.stage("reviewer"):
            require_approved_inputs(run)
            def record_verdicts(verdicts, draft=number):
                run.add("reviewer", "model_result", draft=draft, model=model, verdicts=verdicts)

            decision = review_candidate(candidate, inputs_path, inputs, evidence, judge, run_kit,
                                        record=record_verdicts)
            write_json(candidate / "review.json", decision)
            if decision["s2_checks"] is not None:
                write_json(candidate / "check_report.json", decision["s2_checks"])
            relative = candidate.relative_to(run.folder)
            captured = [str(relative / name) for name in REPORT_FILES if (candidate / name).is_file()]
            run.add("reviewer", "check", draft=number, status=decision["status"],
                    findings=decision["findings"], artifact=str(relative / "review.json"),
                    hashes=digests(run.folder, captured))
        if decision["status"] == "pass":
            with run.stage("prepare_final_review"), TemporaryDirectory(dir=run.folder) as staging:
                require_approved_inputs(run)
                prepared = Path(staging) / "report"
                shutil.copytree(candidate, prepared)
                names = REPORT_FILES
                if "context" in evidence:
                    brief = read_json(prepared / "analysis.json")
                    (prepared / "analysis.md").write_text(brief_markdown(brief), encoding="utf-8")
                    names += ("analysis.md",)
                hashes = {f"report/{name}": digest for name, digest in digests(prepared, names).items()}
                prepared.rename(run.folder / "report")
            run.transition("awaiting_final_approval", approved_draft=number, artifacts=hashes)
            return
        if number > MAX_REVISIONS:
            run.transition("review_escalated", draft=number, findings=decision["findings"])
            return

        run.transition("needs_revision", draft=number, findings=decision["findings"])
        revised = drafts / f"{number + 1:02d}"
        workers = {item["worker"] for item in decision["findings"]}
        with run.stage("revision"):
            require_approved_inputs(run)
            run.add("revision", "assigned", workers=sorted(workers), from_draft=number, to_draft=number + 1)
            if "report" in workers:
                render_draft(run_kit, data_dir, inputs_path, revised, candidate / "analysis.json")
            else:
                ignored = shutil.ignore_patterns("review.json", "check_report.json")
                shutil.copytree(candidate, revised, ignore=ignored)
            if "analyst" in workers:
                feedback = [item for item in decision["findings"] if item["worker"] == "analyst"]
                write_json(revised / "analysis.json", make_brief(evidence, judge, model, feedback=feedback))
        candidate = revised


def approve_outline(folder, kit, data_dir, judge=None, model=None):
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
        require_approved_inputs(run)
        inputs, evidence = read_json(inputs_path), read_json(folder / "evidence.json")
        compare_outline(inputs, evidence, NewsRequest(**run.trace["request"]), snapshot=run.trace["snapshot"])
        if "context.json" in run.trace["outline_digests"]:
            evidence["context"] = read_json(folder / "context.json")
        if evidence["data_available"] and judge is None:
            raise PilotError("Configure the reviewer backend for covered reports")
        run.transition("rendering", approval="outline", approved=run.trace["outline_digests"])
        def run_kit(*args, **kwargs):
            return kit_command(kit, *args, **kwargs)

        review_and_revise(run, run_kit, data_dir, inputs_path, inputs, evidence, judge, model)
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
    start.add_argument("--web-mcp-url", help="optional streamable HTTP endpoint for search_web")
    start.add_argument("--kb-mcp-url", help="optional streamable HTTP endpoint for retrieve_knowledge")
    start.add_argument("--analyst-base", required=True, help="OpenAI-compatible endpoint base")
    start.add_argument("--analyst-model", required=True)
    start.add_argument("--analyst-key-env", help="environment variable containing the analyst key")
    start.add_argument("--thinking-off", action="store_true", help="send the Qwen chat template option")
    approve.add_argument("--reviewer-base", help="OpenAI-compatible reviewer endpoint base")
    approve.add_argument("--reviewer-model", help="reviewer model ID")
    approve.add_argument("--reviewer-key-env", help="environment variable containing the reviewer key")
    approve.add_argument("--thinking-off", action="store_true", help="send the Qwen chat template option")
    args = parser.parse_args(argv)
    folder, data_dir = args.run.resolve(), args.data_dir.resolve()
    try:
        if args.command == "start":
            key = os.environ.get(args.analyst_key_env) if args.analyst_key_env else None
            if args.analyst_key_env and not key:
                raise PilotError(f"Set the analyst credential in {args.analyst_key_env}")
            chat = openai_analyst(args.analyst_base, args.analyst_model, key, thinking_off=args.thinking_off)
            request = NewsRequest(args.keyword.strip(), args.start, args.end)
            trace = start_run(folder, request, args.mcp_url, args.kit, data_dir, chat, args.analyst_model,
                              web_url=args.web_mcp_url, kb_url=args.kb_mcp_url)
        else:
            if bool(args.reviewer_base) != bool(args.reviewer_model):
                raise PilotError("Provide both --reviewer-base and --reviewer-model")
            key = os.environ.get(args.reviewer_key_env) if args.reviewer_key_env else None
            if args.reviewer_key_env and not key:
                raise PilotError(f"Set the reviewer credential in {args.reviewer_key_env}")
            judge = None
            if args.reviewer_base:
                judge = openai_analyst(args.reviewer_base, args.reviewer_model, key,
                                       thinking_off=args.thinking_off)
            trace = approve_outline(folder, args.kit, data_dir, judge, args.reviewer_model)
    except Exception as error:
        print(f"s3 pilot: {failure_message(error)}", file=sys.stderr)
        return 1
    print(json.dumps({"run_id": trace["run_id"], "status": trace["status"],
                      "trace": str(folder / "trace.json")}))
    return 1 if trace["status"] == "review_escalated" else 0


if __name__ == "__main__":
    raise SystemExit(main())
