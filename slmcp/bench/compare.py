"""Summarise and compare benchmark runs: per-run pass totals, mean and spread, per-type rates, correct-abstain
rate, time, HTTP errors, context fit, and paired tests between two configurations.

    python -m slmcp.bench.compare --config qwen=runs/q1.jsonl,runs/q2.jsonl,runs/q3.jsonl \\
        --config torcons=runs/t1.jsonl,... [--ids-file subset.txt] [--md out.md]

Paired tests use the same items in both configurations. An item counts as passed in a configuration when it
passed in the majority of that configuration's runs. McNemar's exact test (two-sided, on the items where the
two configurations disagree) and a bootstrap 95% interval for the difference in pass rate (items resampled,
fixed seed) are reported.
"""
import argparse
import math
import random
import statistics
from collections import defaultdict
from itertools import combinations

from slmcp.bench.check import load_items
from slmcp.bench.score import score

CONTEXTS = (16384, 32768)


def load_config(questions, files, ids=None):
    items = load_items(questions)
    if ids:
        keep = set(ids)
        items = [i for i in items if i["id"] in keep]
    runs = []
    for f in files:
        raw = load_items(f)
        if ids:
            raw = [r for r in raw if r["id"] in keep]
        runs.append({"file": f, "raw": raw, "scores": score(items, raw)})
    return items, runs


def mcnemar_exact(b, c):
    """Two-sided exact McNemar p-value from the discordant counts b and c."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def bootstrap_diff(a, b, reps=2000, seed=7):
    """95% bootstrap interval for mean(a) - mean(b) over paired items (lists of 0/1)."""
    rng, n, diffs = random.Random(seed), len(a), []
    for _ in range(reps):
        idx = [rng.randrange(n) for _ in range(n)]
        diffs.append(sum(a[i] - b[i] for i in idx) / n)
    diffs.sort()
    return diffs[int(0.025 * reps)], diffs[int(0.975 * reps) - 1]


def majority(runs):
    """item id -> 1 if it passed in the majority of runs."""
    votes = defaultdict(list)
    for r in runs:
        for s in r["scores"]:
            votes[s["id"]].append(bool(s["pass"]))
    return {i: int(sum(v) * 2 > len(v)) for i, v in votes.items()}


def summary(name, items, runs):
    totals = [sum(bool(s["pass"]) for s in r["scores"]) for r in runs]
    n = len(items)
    per_type = defaultdict(list)
    abstain, secs, http, fits = [], [], 0, {c: [] for c in CONTEXTS}
    by_item = {i["id"]: i for i in items}
    for r in runs:
        for s in r["scores"]:
            per_type[s["type"]].append(bool(s["pass"]))
            if by_item[s["id"]]["abstain"]:
                abstain.append(bool(s["pass"]))
        for raw in r["raw"]:
            if raw.get("seconds") is not None:
                secs.append(raw["seconds"])
            if str(raw.get("stopped", "")).startswith("http_error"):
                http += 1
            if raw.get("max_prompt_tokens"):
                for c in CONTEXTS:
                    fits[c].append(raw["max_prompt_tokens"] <= c - 1024)  # leave room for the answer
    return {"name": name, "n": n, "runs": len(runs), "totals": totals,
            "mean": statistics.mean(totals) if totals else 0,
            "sd": statistics.stdev(totals) if len(totals) > 1 else 0.0,
            "per_type": {t: sum(v) / len(v) for t, v in sorted(per_type.items())},
            "abstain": sum(abstain) / len(abstain) if abstain else None,
            "median_s": round(statistics.median(secs), 1) if secs else None, "http_errors": http,
            "fit": {c: (sum(v) / len(v) if v else None) for c, v in fits.items()}}


def compare(a_name, a_runs, b_name, b_runs):
    ma, mb = majority(a_runs), majority(b_runs)
    ids = sorted(set(ma) & set(mb))
    x, y = [ma[i] for i in ids], [mb[i] for i in ids]
    b = sum(1 for i, j in zip(x, y, strict=True) if i and not j)
    c = sum(1 for i, j in zip(x, y, strict=True) if j and not i)
    lo, hi = bootstrap_diff(x, y)
    return {"a": a_name, "b": b_name, "n": len(ids), "a_rate": sum(x) / len(ids), "b_rate": sum(y) / len(ids),
            "a_only": b, "b_only": c, "p": mcnemar_exact(b, c), "ci": (lo, hi)}


def to_markdown(sums, comps):
    pct = lambda v: "–" if v is None else f"{100 * v:.1f}%"  # noqa: E731
    out = ["| config | runs | pass per run | mean | sd | correct abstain | median s | http errors | "
           "fits 16k | fits 32k |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for s in sums:
        out.append(f"| {s['name']} | {s['runs']} | {', '.join(map(str, s['totals']))} (of {s['n']}) | "
                   f"{s['mean']:.1f} | {s['sd']:.2f} | {pct(s['abstain'])} | {s['median_s']} | "
                   f"{s['http_errors']} | {pct(s['fit'][16384])} | {pct(s['fit'][32768])} |")
    types = sorted({t for s in sums for t in s["per_type"]})
    out += ["", "| type | " + " | ".join(s["name"] for s in sums) + " |", "|---" * (len(sums) + 1) + "|"]
    for t in types:
        out.append(f"| {t} | " + " | ".join(pct(s["per_type"].get(t)) for s in sums) + " |")
    if comps:
        out += ["", "| A vs B | items | A pass | B pass | only A | only B | McNemar p | 95% CI (A−B) |",
                "|---|---|---|---|---|---|---|---|"]
        for c in comps:
            out.append(f"| {c['a']} vs {c['b']} | {c['n']} | {pct(c['a_rate'])} | {pct(c['b_rate'])} | "
                       f"{c['a_only']} | {c['b_only']} | {c['p']:.3g} | "
                       f"{100 * c['ci'][0]:+.1f} to {100 * c['ci'][1]:+.1f} pts |")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="summarise and compare benchmark runs")
    ap.add_argument("--questions", default="benchmark/questions-v1.1.jsonl")
    ap.add_argument("--config", action="append", required=True, help="name=run1.jsonl,run2.jsonl,...")
    ap.add_argument("--ids-file", help="only these ids (e.g. benchmark/subset-thinking-60.txt)")
    ap.add_argument("--pairs", nargs="*", help="name_a:name_b pairs to test (default: every pair)")
    ap.add_argument("--md")
    args = ap.parse_args(argv)
    ids = open(args.ids_file).read().split() if args.ids_file else None
    configs = {}
    for c in args.config:
        name, files = c.split("=", 1)
        configs[name] = load_config(args.questions, files.split(","), ids)
    sums = [summary(n, items, runs) for n, (items, runs) in configs.items()]
    pairs = [p.split(":") for p in args.pairs] if args.pairs else list(combinations(configs, 2))
    comps = [compare(a, configs[a][1], b, configs[b][1]) for a, b in pairs]
    md = to_markdown(sums, comps)
    print(md)
    if args.md:
        with open(args.md, "w", encoding="utf-8") as f:
            f.write(md + "\n")


if __name__ == "__main__":
    main()
