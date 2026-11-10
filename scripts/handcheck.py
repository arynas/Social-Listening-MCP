"""Hand check of the model's sentiment labels.

    python scripts/handcheck.py make --labelled data/snapshot/data/posts-2026-10.labelled.jsonl \\
        --out-dir ~/Documents/sharing/arynas/arynas-agentic/foundation
    python scripts/handcheck.py compare --sheet .../handcheck_100.csv --key .../handcheck_key.csv

make: picks 100 rows (fixed seed), spread across the three labels and across days, and writes
handcheck_100.csv (no, tanggal, sumber, judul, label_ari: empty) and handcheck_key.csv (the model's labels,
kept apart so the person labels blind). compare: prints the match rate and a confusion table.
"""
import argparse
import csv
import json
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

WIB = ZoneInfo("Asia/Jakarta")
TO_ID = {"positive": "positif", "negative": "negatif", "neutral": "netral"}
ALIASES = {"positif": "positif", "pos": "positif", "p": "positif", "+": "positif",
           "negatif": "negatif", "neg": "negatif", "n": "negatif", "-": "negatif",
           "netral": "netral", "net": "netral", "0": "netral", "t": "netral"}
ORDER = ["positif", "negatif", "netral"]


def pick(rows, n=100, seed=7):
    """n labelled rows: about n/3 per label, and within each label spread across days."""
    rng = random.Random(seed)
    by_label = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row.get("sentiment") in TO_ID:
            day = datetime.fromisoformat(row["date"]).astimezone(WIB).date()
            by_label[TO_ID[row["sentiment"]]][day].append(row)
    chosen = []
    labels = [lab for lab in ORDER if by_label[lab]]
    for k, label in enumerate(labels):
        want = n // len(labels) + (1 if k < n % len(labels) else 0)
        days = {d: rng.sample(rs, len(rs)) for d, rs in sorted(by_label[label].items())}
        while want and any(days.values()):
            for d in list(days):
                if want and days[d]:
                    chosen.append((label, days[d].pop()))
                    want -= 1
    rng.shuffle(chosen)
    return chosen


def make(labelled, out_dir, n=100, seed=7):
    text = Path(labelled).read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    chosen = pick(rows, n, seed)
    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    sheet, key = out / "handcheck_100.csv", out / "handcheck_key.csv"
    # utf-8-sig so Excel and Numbers show Indonesian text correctly
    with open(sheet, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["no", "tanggal", "sumber", "judul", "label_ari"])
        for i, (_, row) in enumerate(chosen, start=1):
            day = datetime.fromisoformat(row["date"]).astimezone(WIB).strftime("%Y-%m-%d %H:%M")
            w.writerow([i, day, row["source"], row["text"], ""])
    with open(key, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["no", "label_model", "url"])
        for i, (label, row) in enumerate(chosen, start=1):
            w.writerow([i, label, row["url"]])
    return sheet, key, Counter(label for label, _ in chosen)


def compare(sheet, key):
    """Returns (matched, checked, confusion {(model, person): n}, blanks, unknown)."""
    with open(sheet, encoding="utf-8-sig", newline="") as f:
        person = {r["no"]: r["label_ari"].strip().lower() for r in csv.DictReader(f)}
    with open(key, encoding="utf-8-sig", newline="") as f:
        model = {r["no"]: r["label_model"] for r in csv.DictReader(f)}
    confusion, blanks, unknown = Counter(), [], []
    for no, label in model.items():
        raw = person.get(no, "")
        if not raw:
            blanks.append(no)
            continue
        if raw not in ALIASES:
            unknown.append((no, raw))
            continue
        confusion[(label, ALIASES[raw])] += 1
    checked = sum(confusion.values())
    matched = sum(v for (m, p), v in confusion.items() if m == p)
    return matched, checked, confusion, blanks, unknown


def print_report(matched, checked, confusion, blanks, unknown):
    if checked:
        print(f"cocok: {matched} dari {checked} ({100 * matched / checked:.1f}%)")
    else:
        print("belum ada label")
    print("\nbaris = label model, kolom = label Ari")
    print(f"{'':10}" + "".join(f"{c:>10}" for c in ORDER))
    for m in ORDER:
        print(f"{m:10}" + "".join(f"{confusion[(m, p)]:>10}" for p in ORDER))
    if blanks:
        print(f"\nbelum diisi: {len(blanks)} baris")
    if unknown:
        print(f"label tidak dikenal: {unknown[:5]}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("--labelled", required=True)
    m.add_argument("--out-dir", required=True)
    m.add_argument("--n", type=int, default=100)
    m.add_argument("--seed", type=int, default=7)
    c = sub.add_parser("compare")
    c.add_argument("--sheet", required=True)
    c.add_argument("--key", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "make":
        sheet, key, mix = make(args.labelled, args.out_dir, args.n, args.seed)
        print(f"wrote {sheet} and {key}; labels: {dict(mix)}")
    else:
        print_report(*compare(args.sheet, args.key))
    return 0


if __name__ == "__main__":
    sys.exit(main())
