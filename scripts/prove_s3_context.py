"""Run and verify the three Krakatau context cases over MCP."""

import argparse
import os
from pathlib import Path

from slmcp.analyst import NewsRequest, openai_analyst
from slmcp.final_review import FinalReviews
from slmcp.orchestrator import approve_outline, read_json, start_run, write_json


def run_case(args, name, web_url, kb_url, analyst, judge, baseline, store):
    folder = args.out / name
    request = NewsRequest("Krakatau", "2026-09-01", "2026-09-14")
    analyst_model = os.environ["ANALYST_MODEL"]
    trace = start_run(folder, request, args.mcp_url, args.kit, args.data_dir, analyst, analyst_model,
                      web_url=web_url, kb_url=kb_url)
    assert trace["status"] == "awaiting_outline_approval"
    assert "context.json" in trace["outline_digests"]
    assert read_json(folder / "outline/report-inputs.json") == baseline
    trace = approve_outline(folder, args.kit, args.data_dir, judge, os.environ["REVIEWER_MODEL"])
    assert trace["status"] == "awaiting_final_approval", (name, trace["steps"][-1])
    report = folder / "report"
    review = read_json(report / "review.json")
    assert review["status"] == "pass" and review["s2_checks"]["issues"] == []
    context = read_json(report / "analysis.json")["context"]
    bundle = context["evidence"]
    claims = context["findings"]
    refs = {ref for item in claims for ref in item["evidence"]}
    assert refs and refs.issubset(bundle["sources"])
    markdown = (report / "analysis.md").read_text(encoding="utf-8")
    for ref in refs:
        assert f"[{ref}](#{ref})" in markdown and bundle["sources"][ref]["url"] in markdown
    assert bundle["services"]["web"] == "ok"
    if name == "relevant":
        assert bundle["services"]["kb"] == "ok" and not bundle["notices"]
        assert {ref.partition("-")[0] for ref in refs} == {"web", "kb"}
    elif name == "conflicting":
        assert bundle["services"]["kb"] == "ok"
        notice, = bundle["notices"]
        assert notice["kind"] == "conflict"
        assert refs.isdisjoint(notice["evidence"])
        assert any(bundle["sources"][ref]["synthetic"] for ref in notice["evidence"])
        assert all(f"[{ref}](#{ref})" in markdown for ref in notice["evidence"])
    else:
        assert bundle["services"]["kb"] == "unavailable"
        assert "Basis pengetahuan tidak tersedia" in markdown
        assert all(ref.startswith("web-") for ref in refs)
    row = store.publish(folder)
    assert store.artifact(row["token"], "analysis.md").decode() == markdown
    assert row["receipt"] is None
    print(f"{name} passed contextual review and awaits final approval", flush=True)
    return {"run_id": trace["run_id"], "services": bundle["services"], "claims": claims,
            "notices": bundle["notices"], "status": trace["status"],
            "snapshot_checks": review["s2_checks"]["status"], "manifest": row["manifest"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", required=True)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--baseline-inputs", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--mcp-url", default="http://127.0.0.1:8769/mcp")
    parser.add_argument("--live", default="http://127.0.0.1:8772/mcp")
    parser.add_argument("--conflict", default="http://127.0.0.1:8773/mcp")
    parser.add_argument("--unavailable", default="http://127.0.0.1:8774/mcp")
    parser.add_argument("--thinking-off", action="store_true")
    args = parser.parse_args()

    def backend(role):
        return openai_analyst(os.environ[f"{role}_BASE_URL"], os.environ[f"{role}_MODEL"],
                              os.getenv(f"{role}_API_KEY"), thinking_off=args.thinking_off)

    analyst, judge = backend("ANALYST"), backend("REVIEWER")
    baseline = read_json(args.baseline_inputs)
    args.out.mkdir(parents=True, exist_ok=False)
    store = FinalReviews(args.out / "pilot.sqlite3")
    cases = {"relevant": (args.live, args.live), "conflicting": (args.conflict, args.conflict),
             "unavailable": (args.live, args.unavailable)}
    summary = {}
    for name, (web_url, kb_url) in cases.items():
        summary[name] = run_case(args, name, web_url, kb_url, analyst, judge, baseline, store)
        write_json(args.out / "summary.json", summary)


if __name__ == "__main__":
    main()
