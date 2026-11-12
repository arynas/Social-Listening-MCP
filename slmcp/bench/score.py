"""Automatic scorer for benchmark runs. Rules are in docs/benchmark.md ("Scoring").

    python -m slmcp.bench.score --run runs/qwen-run1.jsonl [--md runs/qwen-run1.md]

Per item it checks: tool used, right period, the rule (numbers exact / abstain / links / no tool),
links only from tool output, warnings told, and numbers in the answer that are not in any tool output.
An item passes when the rule passes and (for answerable items) the tool was used with the right period.
Everything the scorer fails is meant to be read by a person with the manual-reading rule.
"""
import argparse
import json
import re
from collections import defaultdict

from slmcp.bench.check import load_items

URL = re.compile(r"https?://[^\s)\]>\"'`<]+")
NUMBER = re.compile(r"(?<![\w.,])\d{1,3}(?:[.,]\d{3})+(?![\d])|\d+(?:[.,]\d+)?")
NO_DATA = re.compile(r"tidak (?:ada|tersedia)|belum (?:ada|tersedia)|tidak memiliki data|data .{0,30}tidak|"
                     r"no data|not available|isn't available|is not available|unavailable|don't have data|"
                     r"do not have data|no information|tidak dapat|tidak bisa|cannot|can't|di luar", re.I)
LABEL_WARNING = re.compile(r"perkiraan|estimasi|kasar|belum diverifikasi|tidak diverifikasi|"
                           r"label.{0,40}model|model.{0,40}label|rough|estimate|not checked|model output",
                           re.I)
HEADLINE_WARNING = re.compile(r"judul|headline", re.I)


def numbers(text):
    """Numbers in a text as floats; '1.346' and '1,346' read as thousands, '55,6' as 55.6."""
    out = []
    for m in NUMBER.findall(text or ""):
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", m):
            out.append(float(re.sub(r"[.,]", "", m)))
        else:
            out.append(float(m.replace(",", ".")))
    return out


def has_number(text_numbers, value, pct=False):
    if pct:
        return any(abs(n - value) < 0.051 or n == round(value) for n in text_numbers)
    return value in text_numbers


def parsed(call):
    try:
        return json.loads(call["result"])
    except (json.JSONDecodeError, TypeError):
        return {}


def period_of(result):
    if result.get("data_available"):
        return [result["start_date"], result["end_date"]]
    if result.get("asked_period"):
        return [result["asked_period"]["start"], result["asked_period"]["end"]]
    return None


def score_item(item, run):
    gold, rule = item["gold"], item["rule"]
    answer, calls = run.get("answer") or "", run.get("tool_calls") or []
    results = [parsed(c) for c in calls]
    tool_text = " ".join(c.get("result") or "" for c in calls)
    nums = numbers(answer)
    with_data = [r for r in results if r.get("data_available")]
    s = {"id": item["id"], "type": item["type"], "lang": item["lang"], "rule": rule,
         "tool_used": bool(calls), "stopped": run.get("stopped")}

    expected_periods = [[gold["start"], gold["end"]]] if "start" in gold else (
        [gold["asked"]] if "asked" in gold else [])
    s["right_period"] = (any(period_of(r) in expected_periods for r in results)
                         if expected_periods else None)

    urls = URL.findall(answer)
    s["links_from_tool"] = all(u.rstrip(".,;") in tool_text for u in urls) if urls else None

    if rule == "count":
        none_found = gold["total"] == 0 and bool(NO_DATA.search(answer))
        s["rule_ok"] = has_number(nums, gold["total"]) or none_found
    elif rule == "sentiment":
        need = [k for k in ("positive", "negative", "neutral") if gold["counts"][k]]
        s["rule_ok"] = all(has_number(nums, gold["pct"][k], pct=True) or has_number(nums, gold["counts"][k])
                           for k in need)
    elif rule == "peak":
        days = {int(d[-2:]) for d in gold["peak_days"]}
        s["rule_ok"] = has_number(nums, gold["peak_count"]) and any(d in nums for d in days)
    elif rule == "compare":
        s["rule_ok"] = all(has_number(nums, v) for v in gold["totals"].values())
    elif rule == "links":
        s["rule_ok"] = bool(urls) and s["links_from_tool"] is True
    elif rule == "abstain":
        s["rule_ok"] = bool(NO_DATA.search(answer)) and not with_data
    elif rule == "no_tool":
        s["rule_ok"] = not with_data
    s["answered_other_period"] = bool(item["abstain"] and with_data)

    if not item["abstain"] and with_data:
        s["warnings_told"] = bool(LABEL_WARNING.search(answer)) and bool(HEADLINE_WARNING.search(answer))
    else:
        s["warnings_told"] = None
    tool_nums = set(numbers(tool_text))
    s["numbers_not_in_tool"] = sorted({n for n in nums if n >= 10 and n not in tool_nums
                                       and not 2000 <= n <= 2100} - {float(x) for x in range(32)})
    if item["abstain"]:
        s["pass"] = s["rule_ok"]
    else:
        s["pass"] = bool(s["rule_ok"] and s["tool_used"] and s["right_period"])
    return s


def score(items, runs):
    by_id = {i["id"]: i for i in items}
    return [score_item(by_id[r["id"]], r) for r in runs if r["id"] in by_id]


def summary(scores):
    def rate(rows, key):
        vals = [r[key] for r in rows if r[key] is not None]
        return f"{sum(vals)}/{len(vals)}" if vals else "–"
    lines = ["| group | n | pass | tool used | right period | rule | links from tool | warnings told |"
             " other period on abstain | answers with numbers not in tool |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    groups = defaultdict(list)
    for s in scores:
        groups["all"].append(s)
        groups[f"lang {s['lang']}"].append(s)
        groups[f"type {s['type']}"].append(s)
    for name in sorted(groups, key=lambda g: (g != "all", not g.startswith("lang"), g)):
        rows = groups[name]
        cells = [rate(rows, k) for k in ("pass", "tool_used", "right_period", "rule_ok", "links_from_tool",
                                         "warnings_told", "answered_other_period")]
        invented = sum(bool(r["numbers_not_in_tool"]) for r in rows)
        lines.append(f"| {name} | {len(rows)} | " + " | ".join(cells) + f" | {invented} |")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="score a benchmark run")
    ap.add_argument("--questions", default="benchmark/questions-v1.jsonl")
    ap.add_argument("--run", required=True)
    ap.add_argument("--md", help="also write the summary and per-item table to this Markdown file")
    args = ap.parse_args(argv)
    scores = score(load_items(args.questions), load_items(args.run))
    table = summary(scores)
    print(table)
    if args.md:
        keys = ("pass", "tool_used", "right_period", "rule_ok", "links_from_tool", "warnings_told")
        rows = ["| id | pass | tool | period | rule | links | warnings | numbers not in tool |",
                "|---" * 8 + "|"]
        rows += ["| " + " | ".join([s["id"], *(str(s[k]) for k in keys), str(s["numbers_not_in_tool"] or "")])
                 + " |" for s in scores]
        with open(args.md, "w", encoding="utf-8") as f:
            f.write(f"# Score: {args.run}\n\n{table}\n\n" + "\n".join(rows) + "\n")


if __name__ == "__main__":
    main()
