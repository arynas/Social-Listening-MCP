"""Shared output step for snapshot builders: dedupe rows by URL, keep rows that fit the schema and
fall in the period, write one JSONL per month and a manifest.json."""
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from slmcp.schema import PostError, from_dict


def write_snapshot(out, rows, start, end, meta):
    """rows: dicts for from_dict, each with an optional "language" and a "keywords" list.
    start, end: the WIB days to keep. meta: extra manifest fields (source, citation, ...).
    Returns the manifest, which is also written to <out>/manifest.json."""
    out = Path(out)
    by_url, skipped, per_keyword = {}, [], Counter()
    fetched = outside = duplicates = 0
    for row in rows:
        fetched += 1
        per_keyword.update(row.get("keywords") or [])
        try:
            post = from_dict(row)
        except PostError as e:
            skipped.append(f"{row.get('url') or '?'}: {e}")
            continue
        if not (start <= post.date_wib.date() <= end):
            outside += 1
            continue
        kept = by_url.get(post.url)
        if kept:
            duplicates += 1
            kept["keywords"] = sorted(set(kept["keywords"]) | set(row.get("keywords") or []))
        else:
            by_url[post.url] = {**post.to_dict(), "language": row.get("language", ""),
                                "keywords": sorted(set(row.get("keywords") or []))}
    months, per_day, per_keyword_unique = {}, Counter(), Counter()
    for row in sorted(by_url.values(), key=lambda r: (r["date"], r["url"])):
        months.setdefault(row["date"][:7], []).append(row)
        per_day[from_dict(row).date_wib.date().isoformat()] += 1
        per_keyword_unique.update(row["keywords"])
    files = {}
    for month, month_rows in months.items():
        path = out / f"posts-{month}.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in month_rows),
                        encoding="utf-8")
        files[path.name] = {"rows": len(month_rows), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest = {
        **meta, "period_wib": [start.isoformat(), end.isoformat()],
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"), "files": files,
        "rows_unique": len(by_url), "rows_fetched": fetched, "duplicates_removed": duplicates,
        "outside_period": outside, "skipped": len(skipped), "skipped_examples": skipped[:5],
        "rows_per_day": dict(sorted(per_day.items())),
        "rows_per_keyword_fetched": dict(per_keyword), "rows_per_keyword_unique": dict(per_keyword_unique),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest
