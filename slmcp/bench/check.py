"""No-model check: call the MCP tools in-process with each item's expected arguments and confirm the gold
answer is reachable (or that the tool says data_available false for abstain items).

    python -m slmcp.bench.check --questions benchmark/questions-v1.1.jsonl
"""
import argparse
import asyncio
import io
import json
import sys

from mcp import Client

from slmcp.config import load_config
from slmcp.server import build_server, setup_logging


def load_items(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def problems(item, results):
    """What does not match between the gold answer and the tool results (empty list = reachable)."""
    gold, out = item["gold"], []
    if item["rule"] == "no_tool":
        return [] if not item["expected"] else ["no_tool item has expected calls"]
    if item["rule"] == "abstain":
        for r in results:
            if r.get("data_available") is not False:
                out.append(f"expected data_available false, got {r.get('data_available')}")
            elif r["must_tell_user"] != gold["must_tell_user"]:
                out.append("must_tell_user differs")
        return out
    r = results[0]
    if not r.get("data_available"):
        return [f"no data: {r.get('must_tell_user') or r.get('error')}"]
    if [r["start_date"], r["end_date"]] != [gold["start"], gold["end"]]:
        out.append(f"period {r['start_date']}..{r['end_date']} != {gold['start']}..{gold['end']}")
    rule = item["rule"]
    if rule in ("count", "links", "sentiment") and r["total"] != gold["total"]:
        out.append(f"total {r['total']} != {gold['total']}")
    if rule == "sentiment" and r["sentiment_total"]["pct"] != gold["pct"]:
        out.append("sentiment pct differs")
    if rule == "links":
        if [s["url"] for s in r["samples"]] != gold["sample_urls"]:
            out.append("sample urls differ")
        heads = {s["url"]: s["text"] for s in r["samples"]}
        ex = gold["examples"]
        if any(heads.get(e["url"]) != e["headline"] for e in ex):
            out.append("example headline/link not in the tool samples")
        if len({gold["stories"][e["url"]] for e in ex}) != len(ex) or len(ex) != gold["links_needed"]:
            out.append("examples are not distinct stories")
    if rule == "peak":
        pts = r["trend"]["points"]
        top = max(p["total"] for p in pts)
        if top != gold["peak_count"] or [p["start"] for p in pts if p["total"] == top] != gold["peak_days"]:
            out.append("peak differs")
    if rule == "compare":
        totals = {c["keyword"]: x["total"] for c, x in zip(item["expected"], results, strict=True)}
        if totals != gold["totals"]:
            out.append(f"totals {totals} != {gold['totals']}")
    return out


async def check_all(config_path, items):
    server = build_server(load_config(config_path))
    report = []
    async with Client(server) as client:
        for item in items:
            results = []
            for call in item["expected"]:
                args = {k: v for k, v in call.items() if k != "tool"}
                res = await client.call_tool(call["tool"], args)
                results.append(json.loads(res.content[0].text))
            found = problems(item, results)
            alt = item["gold"].get("alt")
            if alt:  # the corrected keyword's total must be reachable too
                call = {k: v for k, v in item["expected"][0].items() if k != "tool"}
                res = await client.call_tool(item["expected"][0]["tool"], {**call, "keyword": alt["keyword"]})
                if json.loads(res.content[0].text).get("total") != alt["total"]:
                    found.append("alt total differs")
            report.append((item["id"], found))
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="no-model check of the benchmark gold answers")
    ap.add_argument("--config", default="config.snapshot.example.yaml")
    ap.add_argument("--questions", default="benchmark/questions-v1.1.jsonl")
    args = ap.parse_args(argv)
    setup_logging(io.StringIO())  # keep the per-call log out of this report
    items = load_items(args.questions)
    report = asyncio.run(check_all(args.config, items))
    bad = [(i, p) for i, p in report if p]
    for i, p in bad:
        print(f"FAIL {i}: {'; '.join(p)}")
    calls = sum(len(i["expected"]) for i in items)
    print(f"checked {len(items)} items ({calls} tool calls through MCP): "
          f"{len(items) - len(bad)} ok, {len(bad)} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
