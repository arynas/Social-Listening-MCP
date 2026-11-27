"""Run the four Krakatau reviewer cases using live MCP and a configured reviewer."""

import argparse
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from llm_report_kit.cli import export_pdf
from pptx import Presentation

from slmcp import orchestrator
from slmcp.analyst import NewsRequest, openai_analyst
from slmcp.orchestrator import read_json, write_json

CASES = {"clean": None, "wrong-count": ("figures", "report"), "incorrect-link": ("links", "report"),
         "unsupported-conclusion": ("unsupported_conclusion", "analyst")}


def seed_defect(candidate, case):
    if case == "unsupported-conclusion":
        brief = read_json(candidate / "analysis.json")
        brief["findings"][-1] = {
            "text": "Letusan Krakatau menyebabkan kerugian ekonomi besar bagi semua maskapai Indonesia.",
            "evidence": ["headlines"],
        }
        write_json(candidate / "analysis.json", brief)
        return
    path = candidate / "report.pptx"
    deck = Presentation(path)
    if case == "wrong-count":
        run = next(run for shape in deck.slides[1].shapes if shape.has_text_frame
                   for paragraph in shape.text_frame.paragraphs for run in paragraph.runs
                   if run.text == "1.976")
        run.text = "1.975"
    else:
        runs = [run for shape in deck.slides[2].shapes if shape.has_text_frame
                for paragraph in shape.text_frame.paragraphs
                for run in paragraph.runs if run.hyperlink.address]
        legitimate = runs[0].hyperlink.address
        affected = 0
        for run in runs:
            if run.hyperlink.address == legitimate:
                run.hyperlink.address = "https://example.invalid/krakatau-wrong-link"
                affected += 1
        assert affected
    deck.save(path)
    with TemporaryDirectory(dir=candidate) as temp:
        folder = Path(temp)
        export_pdf(path, folder, folder).replace(candidate / "report.pdf")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--pilot-brief", type=Path, required=True, help="clean analysis.json from S3-1")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mcp-url", default="http://127.0.0.1:8769/mcp")
    parser.add_argument("--thinking-off", action="store_true")
    args = parser.parse_args()
    reviewer_model = os.environ["REVIEWER_MODEL"]
    judge = openai_analyst(os.environ["REVIEWER_BASE_URL"], reviewer_model, os.getenv("REVIEWER_API_KEY"),
                           thinking_off=args.thinking_off)
    clean = read_json(args.pilot_brief)
    response = json.dumps({"findings": clean["findings"]}, ensure_ascii=False)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    verify = orchestrator.review_candidate

    def prepare_review(candidate, *options):
        case = candidate.parent.parent.name
        if case != "clean" and candidate.name == "01":
            seed_defect(candidate, case)
        if candidate.name == "02":
            assert read_json(candidate.parent.parent / "trace.json")["status"] == "needs_revision"
            assert not (candidate.parent.parent / "report").exists()
        return verify(candidate, *options)

    summary = {}
    with patch.object(orchestrator, "review_candidate", side_effect=prepare_review):
        for case, expected in CASES.items():
            folder = out / case
            request = NewsRequest("Krakatau", "2026-09-01", "2026-09-14")
            trace = orchestrator.start_run(folder, request, args.mcp_url, args.kit, args.data_dir,
                                           lambda _: response, clean["model"])
            assert trace["status"] == "awaiting_outline_approval" and not (folder / "report").exists()
            trace = orchestrator.approve_outline(folder, args.kit, args.data_dir, judge, reviewer_model)
            checks = [step for step in trace["steps"]
                      if step["event"] == "check" and step["stage"] == "reviewer"]
            assert trace["status"] == "awaiting_final_approval", (case, trace["status"], checks)
            assert checks[-1]["status"] == "pass" and checks[-1]["findings"] == []
            initial = checks[0]
            revisions = [step for step in trace["steps"] if step["event"] == "assigned"]
            if expected is None:
                assert initial["status"] == "pass" and not revisions
            else:
                kind, worker = expected
                assert initial["status"] == "needs_revision"
                assert any((item["kind"], item["worker"]) == (kind, worker) for item in initial["findings"])
                assert len(revisions) == 1 and revisions[0]["workers"] == [worker]
                assert any(step.get("status") == "needs_revision" for step in trace["steps"])
                if worker == "analyst":
                    for artifact in ("report.pptx", "report.pdf"):
                        before = (folder / "drafts/01" / artifact).read_bytes()
                        assert before == (folder / "report" / artifact).read_bytes()
            check_report = read_json(folder / "report/check_report.json")
            assert check_report["expected"]["total"] == 1976 and check_report["status"] == "pass"
            summary[case] = {"run_id": trace["run_id"], "initial": initial["status"],
                             "findings": initial["findings"], "revisions": len(revisions),
                             "final": trace["status"]}
            print(case, initial["status"], f"revisions={len(revisions)}", trace["status"], flush=True)
    write_json(out / "summary.json", summary)


if __name__ == "__main__":
    main()
