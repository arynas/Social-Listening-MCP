"""A fixed, stratified subset of the benchmark (for slower settings such as thinking on).

    python -m slmcp.bench.subset --n-pairs 30 --seed 60 --out benchmark/subset-thinking-60.txt

Pairs (Indonesian + English) stay together. Every question type gets at least one pair; the rest are
shared out in proportion to the type's size (largest remainder), then picked at random with a fixed seed.
"""
import argparse
import random
from collections import defaultdict

from slmcp.bench.check import load_items


def stratified_pairs(items, n_pairs=30, seed=60):
    by_type = defaultdict(list)
    for i in items:
        if i["lang"] == "id":
            by_type[i["type"]].append(i["pair_id"])
    types = sorted(by_type)
    total = sum(len(v) for v in by_type.values())
    share = {t: 1 + (n_pairs - len(types)) * len(by_type[t]) / total for t in types}
    count = {t: int(share[t]) for t in types}
    for t in sorted(types, key=lambda t: (-(share[t] - count[t]), t))[: n_pairs - sum(count.values())]:
        count[t] += 1
    rng = random.Random(seed)
    return sorted(p for t in types for p in rng.sample(sorted(by_type[t]), count[t]))


def subset_ids(items, n_pairs=30, seed=60):
    pairs = set(stratified_pairs(items, n_pairs, seed))
    return [i["id"] for i in items if i["pair_id"] in pairs]


def main(argv=None):
    ap = argparse.ArgumentParser(description="write a stratified subset of benchmark ids")
    ap.add_argument("--questions", default="benchmark/questions-v1.1.jsonl")
    ap.add_argument("--n-pairs", type=int, default=30)
    ap.add_argument("--seed", type=int, default=60)
    ap.add_argument("--out", default="benchmark/subset-thinking-60.txt")
    args = ap.parse_args(argv)
    ids = subset_ids(load_items(args.questions), args.n_pairs, args.seed)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(ids) + "\n")
    print(f"{len(ids)} ids -> {args.out}")


if __name__ == "__main__":
    main()
