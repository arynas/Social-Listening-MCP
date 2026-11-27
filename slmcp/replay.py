"""Replay saved analyst decisions without contacting tools, models, or delivery services."""

import argparse
import json
import sys
from pathlib import Path

from slmcp.analyst import DATA_TOOLS, NewsRequest, PilotError, checked_tool_payload, make_brief, reconcile
from slmcp.context import brief_markdown, context_notices, source_passage
from slmcp.orchestrator import (
    APPROVED_FILES,
    MAX_REVISIONS,
    PILOT_TZ,
    REPORT_FILES,
    compare_outline,
    digests,
    failure_message,
    read_json,
    write_json,
)
from slmcp.reviewer import check_analysis, check_rendered


class ReplayError(PilotError):
    def __init__(self, message, step):
        super().__init__(message)
        self.location = {key: step[key] for key in ("seq", "stage", "event", "tool", "draft") if key in step}


def require(condition, message, step):
    if not condition:
        raise ReplayError(message, step)


def bundle_path(root, name):
    relative = Path(name)
    path = root / relative
    if relative.is_absolute() or ".." in relative.parts or not path.resolve().is_relative_to(root):
        raise PilotError("A replay artifact path escapes the run directory")
    return path


def verify_hashes(root, hashes, step, required=()):
    require(isinstance(hashes, dict) and set(required) <= hashes.keys(),
            "The trace lacks a required artifact hash", step)
    for name, expected in hashes.items():
        try:
            bundle_path(root, name)
            actual = digests(root, (name,))[name]
        except (ValueError, OSError) as error:
            raise ReplayError(f"Cannot read replay artifact {name}: {error}", step) from error
        require(expected == actual, f"Artifact digest differs for {name}", step)


def replay_data(root, steps, request, version, outcome):
    events = [step for step in steps if step["stage"] == "data"
              and step["event"] in ("tool_call", "tool_result")]
    require(bool(events), "No data tool call was recorded", steps[-1])
    failed = next((step for step in steps if step["stage"] == "data" and step["event"] == "failed"), None)
    replies = []
    for index in range(0, len(events), 2):
        call = events[index]
        require(index // 2 < len(DATA_TOOLS), "Unexpected additional data call", call)
        tool = DATA_TOOLS[index // 2]
        require(call["event"] == "tool_call" and call.get("tool") == tool
                and call.get("arguments") == request.tool_args(), "The recorded data call differs", call)
        require(index + 1 < len(events), "The data call has no saved response", call)
        result = events[index + 1]
        require(result["event"] == "tool_result" and result.get("tool") == tool,
                "The response does not match its data call", result)
        state = result.get("status")
        if state in ("error", "invalid_response"):
            require(failed is not None and outcome == "failed" and index + 2 == len(events),
                    "The rejected data call did not stop the run", result)
            if state == "error":
                try:
                    checked_tool_payload(tool, {"error": result["problem"]})
                except PilotError as error:
                    message = str(error)
                else:
                    raise ReplayError("The recorded rejection lacks a problem", result)
            else:
                message = f"{tool} did not return a report result"
            require(failed.get("error") == message, "The replayed data error differs", failed)
            require(not any(step["stage"] == "analyst" and step["event"] == "started" for step in steps),
                    "Analysis was started after a rejected data call", result)
            return None, {"step": result["seq"], "stage": "data", "tool": tool, "message": message}
        require(state == "ok", "Unrecognized data tool result", result)
        if version >= 2:
            payload = result.get("payload")
            require(isinstance(payload, dict) and not payload.get("error"),
                    "The successful tool event lacks its saved payload", result)
            replies.append(checked_tool_payload(tool, payload))

    require(len(events) == 4, "Expected both recorded data responses", events[-1])
    if version >= 2:
        try:
            evidence = reconcile(request, *replies)
        except (ValueError, KeyError, TypeError) as error:
            require(failed is not None and outcome == "failed" and failed.get("error") == str(error),
                    f"Saved tool results failed reconciliation: {error}", events[-1])
            return None, {"step": failed["seq"], "stage": "data", "message": str(error)}
        require(evidence == read_json(root / "evidence.json"),
                "Reconciled evidence differs from the saved evidence", events[-1])
    else:
        evidence = read_json(root / "evidence.json")
        for result in events[1::2]:
            require(result["data_available"] == evidence["data_available"]
                    and result.get("total") == evidence["facts"].get("total"),
                    "Legacy evidence differs from the recorded tool summary", result)
    return evidence, None


def replay_brief(candidate, evidence, step):
    original = read_json(candidate / "analysis.json")
    context = original.get("context", {})
    response = {"findings": original["findings"], "context_findings": context.get("findings")}
    rebuilt = make_brief(evidence, lambda _: json.dumps(response), original["model"])
    attempts = original.get("attempts")
    require(attempts in ((1, 2) if evidence["data_available"] else (0,)),
            "The recorded analyst attempt count is invalid", step)
    rebuilt["attempts"] = attempts
    require(rebuilt == original, "The saved brief differs from the validated analyst output", step)


def replay_review(root, steps, event, inputs, evidence, audit, version):
    draft = event["draft"]
    candidate = bundle_path(root, f"drafts/{draft:02d}")
    approved = root / "outline/report-inputs.json"
    expected = candidate / "review.json"
    require(event["artifact"] == str(expected.relative_to(root)), "Unexpected review artifact path", event)
    files = [str((candidate / name).relative_to(root)) for name in REPORT_FILES]
    required = [name for name in files if bundle_path(root, name).is_file()]
    if version >= 2:
        verify_hashes(root, event.get("hashes"), event, required)
    saved = read_json(expected)

    def check_saved(_command, *args, accepted):
        arguments = dict(zip(args[::2], args[1::2], strict=True))
        if audit is None:
            result = read_json(candidate / "check_report.json")
        else:
            attribution = inputs.get("attribution", {}).get("url", "https://www.gdeltproject.org/")
            result = audit(inputs, arguments["--pptx"], arguments["--pdf"], attribution)
        write_json(arguments["--out"], result)
        code = int(result["status"] != "pass")
        require(code in accepted, "Unexpected local checker result", event)
        return code

    checks, report_findings = check_rendered(candidate, approved, inputs, check_saved)
    require(checks == saved["s2_checks"], "Replayed report checks differ from the saved result", event)
    analysis_findings = []
    if not report_findings:
        verdicts = [step for step in steps if step["stage"] == "reviewer" and step["event"] == "model_result"
                    and step.get("draft") == draft]
        if version >= 2:
            def recorded_judge(_):
                require(len(verdicts) == 1 and verdicts[0]["seq"] < event["seq"],
                        "The draft has no single saved reviewer verdict", event)
                return json.dumps({"verdicts": verdicts[0]["verdicts"]})
            analysis_findings = check_analysis(candidate, evidence, recorded_judge)
        else:
            # Earlier traces retained semantic findings rather than model verdicts.
            analysis_findings = [finding for finding in saved["findings"] if finding["worker"] == "analyst"]
            if not analysis_findings:
                replay_brief(candidate, evidence, event)

    findings = report_findings + analysis_findings
    status = "needs_revision" if findings else "pass"
    require(findings == saved["findings"] == event["findings"]
            and status == saved["status"] == event["status"]
            and saved["analysis_reviewed"] is (not report_findings),
            "Replayed reviewer findings or routing differ from the saved decision", event)
    return findings


def replay_run(root, *, audit=None):
    root = root.resolve()
    trace = read_json(bundle_path(root, "trace.json"))
    version = trace["schema_version"]
    if version not in (1, 2):
        raise PilotError(f"Unsupported trace version {version}")
    if trace["timezone"] != PILOT_TZ:
        raise PilotError("The recorded timezone is unsupported")
    steps = []
    for index, original in enumerate(trace["steps"], 1):
        step = {"seq": index, **original}
        require(step["seq"] == index and (version == 1 or "seq" in original),
                "The recorded step sequence is broken", step)
        steps.append(step)
    if not steps:
        raise PilotError("The trace has no steps")
    config = trace.get("configuration", {})
    limit = config.get("max_revisions", MAX_REVISIONS)
    require(type(limit) is int and limit >= 0 and (version == 1 or "slmcp_version" in config),
            "The trace has no valid replay configuration", steps[0])

    request = NewsRequest(**trace["request"])
    require(steps[0]["event"] == "input" and steps[0].get("request") == trace["request"],
            "The first step does not match the trace input", steps[0])
    state = trace["status"]
    summary = {"run_id": trace["run_id"], "trace_version": version, "recorded_status": state,
               "data_source": "recorded_tool_results" if version >= 2 else "legacy_record",
               "report_checks": "recomputed_locally" if audit else "recorded_checks",
               "reviewer_model": "recorded_verdicts" if version >= 2 else "recorded_findings",
               "failures": []}
    if version >= 2:
        replay_inputs = {"evidence.json", "analysis.json", "context.json"}
        for step in steps:
            if step["event"] == "artifact" and step.get("path") in replay_inputs:
                verify_hashes(root, step.get("hashes"), step, (step["path"],))

    evidence, data_failure = replay_data(root, steps, request, version, state)
    if data_failure:
        summary["failures"].append(data_failure)
        return summary

    decisions = [step for step in steps if step["stage"] == "orchestrator" and step["event"] == "decision"]
    require(bool(decisions) and decisions[0].get("status") == "awaiting_outline_approval",
            "The trace did not reach the recorded outline decision", steps[-1])
    outline = decisions[0]
    hashes = trace["outline_digests"]
    verify_hashes(root, hashes, outline, APPROVED_FILES)
    inputs = read_json(root / "outline/report-inputs.json")
    try:
        compare_outline(inputs, evidence, request, snapshot=trace["snapshot"])
    except PilotError as error:
        raise ReplayError(str(error), outline) from error
    if "context.json" in hashes:
        context = read_json(root / "context.json")
        sources = {ref: source_passage(doc) for ref, doc in context["sources"].items()}
        require(context == {"sources": sources, "services": context["services"],
                            "notices": context_notices(context["services"], sources)},
                "The saved context or notices differ", outline)
        evidence["context"] = context
    replay_brief(root, evidence, outline)
    summary["data_available"] = evidence["data_available"]
    summary["headline_total"] = evidence["facts"].get("total")
    if state == "awaiting_outline_approval":
        require(len(decisions) == 1, "The outline-only trace contains later decisions", decisions[-1])
        return summary
    require(len(decisions) >= 3 and decisions[1].get("status") == "rendering"
            and decisions[1].get("approval") == "outline" and decisions[1].get("approved") == hashes,
            "Rendering lacks a recorded approval for this outline", decisions[-1])

    reviews = [step for step in steps if step["stage"] == "reviewer" and step["event"] == "check"]
    require(0 < len(reviews) == len(decisions) - 2, "The review and decision counts differ", decisions[-1])
    revisions = [step for step in steps if step["stage"] == "revision" and step["event"] == "assigned"]
    revision_count = 0
    for number, (review, decision) in enumerate(zip(reviews, decisions[2:], strict=True), 1):
        require(review.get("draft") == number and review["seq"] < decision["seq"],
                "Draft reviews appear out of order", review)
        try:
            findings = replay_review(root, steps, review, inputs, evidence, audit, version)
        except ReplayError:
            raise
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ReplayError(str(error), review) from error
        expected = ("review_escalated" if number > limit else "needs_revision") if findings else (
            "awaiting_final_approval")
        if findings:
            summary["failures"].append({"step": review["seq"], "stage": "reviewer", "draft": number,
                                        "findings": findings})
        require(decision["status"] == expected, "The replayed review leads to a different decision", decision)
        if expected == "needs_revision":
            require(revision_count < len(revisions) and number < len(reviews),
                    "The assigned revision or its review was not recorded", decision)
            assigned = revisions[revision_count]
            revision_count += 1
            require(assigned.get("from_draft") == number and assigned.get("to_draft") == number + 1
                    and assigned.get("workers") == sorted({item["worker"] for item in findings})
                    and decision["seq"] < assigned["seq"] < reviews[number]["seq"],
                    "The recorded revision differs from the replayed routing", assigned)
        else:
            require(number == len(reviews), "The trace continued beyond the review outcome", decision)
    last = decisions[-1]
    require(last["status"] == state and revision_count == len(revisions),
            "Recorded final state differs", last)
    if state == "awaiting_final_approval":
        require(last.get("approved_draft") == len(reviews), "Final artifact points to the wrong draft", last)
        required = [f"report/{name}" for name in REPORT_FILES]
        if "context" in evidence:
            required.append("report/analysis.md")
        verify_hashes(root, last.get("artifacts"), last, required if version >= 2 else ())
        for name in REPORT_FILES:
            approved = root / f"drafts/{len(reviews):02d}" / name
            require((root / "report" / name).read_bytes() == approved.read_bytes(),
                    f"Final report differs from the approved draft in {name}", last)
        if "context" in evidence:
            brief = brief_markdown(read_json(root / "report/analysis.json"))
            require((root / "report/analysis.md").read_text() == brief,
                    "Final citations differ from the reviewed analysis", last)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="run folder with trace.json and artifacts")
    parser.add_argument("--recheck", action="store_true", help="recompute the report checks locally")
    args = parser.parse_args(argv)
    try:
        audit = None
        if args.recheck:
            from llm_report_kit.verify import audit
        result = replay_run(args.run, audit=audit)
    except (ImportError, KeyError, OSError, ValueError, TypeError, IndexError) as error:
        result = {"error": failure_message(error), "location": getattr(error, "location", None)}
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
