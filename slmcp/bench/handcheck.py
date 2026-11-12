"""Write a seeded sample of benchmark items to a CSV for a person to check: is the question clear, and is
the gold answer right?

    python -m slmcp.bench.handcheck --out handcheck-30.csv --n 30 --seed 30

The sample mixes types and languages: items are taken round-robin over the types, alternating
Indonesian and English.
"""
import argparse
import csv
import random
from collections import defaultdict

from slmcp.bench.check import load_items

MONTHS = ("Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des")
COLUMNS = ["id", "question", "gold answer", "clear? (y/n)", "gold right? (y/n)", "note"]


def day(iso):
    y, m, d = iso.split("-")
    return f"{int(d)} {MONTHS[int(m) - 1]} {y}"


def span(gold):
    a, b = gold["start"], gold["end"]
    return day(a) if a == b else f"{day(a)} – {day(b)}"


def gold_text(item):
    """The gold answer in plain words (Indonesian) for the checker, with the date context first."""
    text = _gold_text(item)
    context = item["gold"].get("context")
    return f"[{context}] {text}" if context else text


def _gold_text(item):
    g, rule = item["gold"], item["rule"]
    if rule == "count":
        alt = g.get("alt")
        extra = (f" (atau, jika jawaban menyebut membaca '{alt['typo']}' sebagai '{alt['keyword']}': "
                 f"{alt['total']}; aturan typo diputuskan Ari 12 Nov 2026)") if alt else ""
        return f"{g['total']} judul berita ({span(g)}){extra}"
    if rule == "sentiment":
        p, c = g["pct"], g["counts"]
        return (f"{g['total']} judul ({span(g)}): positif {p['positive']}% ({c['positive']}), "
                f"negatif {p['negative']}% ({c['negative']}), netral {p['neutral']}% ({c['neutral']})")
    if rule == "peak":
        return f"puncak {', '.join(day(d) for d in g['peak_days'])}: {g['peak_count']} judul ({span(g)})"
    if rule == "compare":
        totals = ", ".join(f"{k} {v}" for k, v in g["totals"].items())
        return f"{totals} ({span(g)}); lebih banyak: {g['larger']}"
    if rule == "links":
        ex = "; ".join(f"{e['headline']} ({e['url']})" for e in g["examples"])
        return (f"{g['total']} judul ({span(g)}). Jawaban harus berisi {g['links_needed']} judul dari cerita "
                f"berbeda, masing-masing dengan link dari hasil tool, misalnya: {ex}")
    if rule == "abstain":
        return "Tidak ada data. " + " ".join(g["must_tell_user"])
    return ("Di luar cakupan, tidak perlu tool. Jawaban biasa lolos; mengatakan di luar data berita "
            "juga lolos. Gagal hanya jika mengarang data berita, atau menyebut harga/prakiraan "
            "masa depan sebagai fakta.")


def sample(items, n=30, seed=30):
    rng = random.Random(seed)
    by_type = defaultdict(list)
    for i in items:
        by_type[i["type"]].append(i)
    for v in by_type.values():
        rng.shuffle(v)
    chosen, used_pairs, lang, types = [], set(), "id", sorted(by_type)
    while len(chosen) < n:
        for t in types:
            pool = [i for i in by_type[t] if i["lang"] == lang and i["pair_id"] not in used_pairs]
            if pool and len(chosen) < n:
                chosen.append(pool[0])
                used_pairs.add(pool[0]["pair_id"])
                lang = "en" if lang == "id" else "id"
    return chosen


def main(argv=None):
    ap = argparse.ArgumentParser(description="write a hand-check sample of the benchmark")
    ap.add_argument("--questions", default="benchmark/questions-v1.1.jsonl")
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=30)
    args = ap.parse_args(argv)
    rows = sample(load_items(args.questions), args.n, args.seed)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for i in rows:
            w.writerow([i["id"], i["question"], gold_text(i), "", "", ""])
    print(f"{len(rows)} items -> {args.out}")


if __name__ == "__main__":
    main()
