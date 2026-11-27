"""Run the S3-5 hard cases for S2 and S3 and pre-score them against gold fixed before the run.

    python scripts/eval_hard_cases.py --gold benchmark/s3-hard-cases.json --baseline S2_KIT --kit KIT \
        --data-dir DATA --out OUT [--context-url http://127.0.0.1:8773/mcp] [--rounds 2] [--thinking-off]

The analyst/reviewer model comes from ANALYST_BASE_URL, ANALYST_MODEL and optional ANALYST_API_KEY.
S3 runs stop at final approval; nothing is delivered.
"""
import argparse
import os
import time
from pathlib import Path

import fitz

from slmcp.analyst import NewsRequest, openai_analyst
from slmcp.hard_cases import load_cases, score_report
from slmcp.orchestrator import approve_outline, kit_command, read_json, start_run, write_json


def pdf_text(path):
    with fitz.open(path) as document:
        return "\n".join(page.get_text() for page in document)


def run_one(args, chat, model, name, case, flow, number):
    root = args.out / f"round-{number}" / name / flow
    request = NewsRequest(case["keyword"], case["start"], case["end"])
    started = time.perf_counter()
    error = None
    try:
        if flow == "S2":
            root.mkdir(parents=True)
            kit_command(args.baseline, "outline", "--data-dir", args.data_dir, "--keyword", request.keyword,
                        "--start", request.start, "--end", request.end, "--out", root / "outline")
            kit_command(args.baseline, "build", "--data-dir", args.data_dir,
                        "--inputs", root / "outline/report-inputs.json", "--out", root / "report")
        else:
            context = {"web_url": args.context_url, "kb_url": args.context_url} if case.get("context") else {}
            trace = start_run(root, request, args.mcp_url, args.kit, args.data_dir, chat, model, **context)
            if trace["status"] != "awaiting_outline_approval":
                raise RuntimeError(f"S3 stopped at {trace['status']}")
            trace = approve_outline(root, args.kit, args.data_dir, chat, model)
            if trace["status"] != "awaiting_final_approval":
                raise RuntimeError(f"S3 stopped at {trace['status']}")
    except Exception as exc:  # recorded as a failed run, scored zero
        error = f"{type(exc).__name__}: {exc}"
    seconds = round(time.perf_counter() - started, 2)
    report = root / "report"
    row = {"case": name, "flow": flow, "round": number, "machine_seconds": seconds, "error": error}
    if error or not (report / "report.pdf").exists():
        return {**row, "score": 0, "criteria": None, "reasons": [error or "no report"]}
    kit_command(args.kit, "check", "--inputs", report / "report-inputs.json", "--pdf", report / "report.pdf",
                "--pptx", report / "report.pptx", "--out", root / "common-check.json", accepted=(0, 1))
    text = pdf_text(report / "report.pdf")
    if (report / "analysis.md").exists():
        text += "\n" + (report / "analysis.md").read_text(encoding="utf-8")
    report_inputs, checks = read_json(report / "report-inputs.json"), read_json(root / "common-check.json")
    scored = score_report(case, report_inputs, checks, text)
    return {**row, **scored}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--gold", required=True, type=Path)
    cli.add_argument("--baseline", required=True, help="S2 0.1.0 kit executable")
    cli.add_argument("--kit", required=True, help="llm-report-kit 0.1.1 executable")
    cli.add_argument("--data-dir", required=True, type=Path)
    cli.add_argument("--out", required=True, type=Path)
    cli.add_argument("--mcp-url", default="http://127.0.0.1:8769/mcp")
    cli.add_argument("--context-url", help="context MCP with the web and conflicting KB passages")
    cli.add_argument("--rounds", type=int, default=2)
    cli.add_argument("--cases", nargs="*", help="subset of case names")
    cli.add_argument("--thinking-off", action="store_true")
    args = cli.parse_args()

    cases = load_cases(args.gold)
    if args.cases:
        cases = {name: cases[name] for name in args.cases}
    model = os.environ["ANALYST_MODEL"]
    chat = openai_analyst(os.environ["ANALYST_BASE_URL"], model, os.getenv("ANALYST_API_KEY"),
                          thinking_off=args.thinking_off)
    args.out.mkdir(parents=True, exist_ok=False)
    rows = []
    for number in range(1, args.rounds + 1):
        for index, (name, case) in enumerate(cases.items()):
            flows = ("S2", "S3") if (number + index) % 2 else ("S3", "S2")
            for flow in flows:
                rows.append(run_one(args, chat, model, name, case, flow, number))
                print(f"round {number} {name} {flow}: score {rows[-1]['score']}", flush=True)
                summary = {"model": model, "gold": str(args.gold), "rows": rows}
                write_json(args.out / "results.json", summary)


if __name__ == "__main__":
    main()
