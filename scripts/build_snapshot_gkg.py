"""Build a frozen snapshot of Indonesian news headlines from GDELT 2.0 raw files (translingual GKG).

GDELT publishes one translingual GKG file every 15 minutes:
https://data.gdeltproject.org/gdeltv2/YYYYMMDDHHMMSS.translation.gkg.csv.zip (tab-separated, 27 columns).
We keep only rows whose source language is Indonesian (TranslationInfo "srclc:ind") and that have a page
title (Extras <PAGE_TITLE>). Stored per row: title (text), URL, domain (source), GDELT time (date, UTC),
language, and the keywords found in the title. No article bodies. GDELT terms
(https://www.gdeltproject.org/about.html): free use and redistribution, with a citation to the GDELT
Project and a link to https://www.gdeltproject.org/.

    python scripts/build_snapshot_gkg.py --start 2026-10-01 --end 2026-10-01 \\
        --keywords banjir "harga beras" gempa --out data/snapshot_pilot

Resumable: each 15-minute file is parsed once into <out>/parts/<stamp>.jsonl; the zip is deleted after
parsing. Polite: one download at a time with a pause in between (default 2 s).
"""
import argparse
import csv
import html
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from slmcp import __version__  # noqa: E402
from slmcp.adapters.base import utc_window  # noqa: E402
from slmcp.snapshot import write_snapshot  # noqa: E402

BASE = "https://data.gdeltproject.org/gdeltv2"
USER_AGENT = f"social-listening-mcp/{__version__} (snapshot builder)"
STEP = timedelta(minutes=15)
# GKG 2.1 columns we use
DATE, DOMAIN, URL, TRANSLATION, EXTRAS = 1, 3, 4, 25, 26
TITLE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.S)
csv.field_size_limit(sys.maxsize)


def stamps(start, end):
    """The 15-minute file stamps (UTC) that cover the WIB days start..end."""
    begin, stop = utc_window(start, end)
    t = begin
    while t < stop:
        yield t.strftime("%Y%m%d%H%M%S")
        t += STEP


def http_get(url, timeout=120):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def parse_gkg(data, keywords, lang="ind"):
    """A translingual GKG zip (bytes) -> rows for the Post schema: Indonesian rows with a title only."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        text = z.read(z.namelist()[0]).decode("utf-8", "replace")
    rows = []
    for rec in csv.reader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE):
        if len(rec) <= EXTRAS or f"srclc:{lang}" not in rec[TRANSLATION]:
            continue
        m = TITLE.search(rec[EXTRAS])
        title = " ".join(html.unescape(m.group(1)).split()) if m else ""
        try:
            when = datetime.strptime(rec[DATE], "%Y%m%d%H%M%S").replace(tzinfo=UTC).isoformat()
        except ValueError:
            when = rec[DATE]
        found = [k for k in keywords if k.lower() in title.lower()]
        rows.append({"date": when, "text": title, "source": rec[DOMAIN], "platform": "news",
                     "url": rec[URL], "language": lang, "keywords": found})
    return rows


def collect(stamps_list, keywords, out, get=http_get, sleep=time.sleep, pause=2.0, log=print):
    """Download and parse every file not yet saved. Returns (missing stamps, bytes downloaded)."""
    parts = Path(out) / "parts"
    parts.mkdir(parents=True, exist_ok=True)
    missing, downloaded, first = [], 0, True
    for stamp in stamps_list:
        path = parts / f"{stamp}.jsonl"
        if path.exists():
            continue
        if not first:
            sleep(pause)
        first = False
        url = f"{BASE}/{stamp}.translation.gkg.csv.zip"
        try:
            data = get(url)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                missing.append(stamp)
                path.write_text("", encoding="utf-8")
                log(f"{stamp}: no file (404)")
                continue
            raise
        downloaded += len(data)
        zip_path = parts / f"{stamp}.zip"
        zip_path.write_bytes(data)
        rows = parse_gkg(zip_path.read_bytes(), keywords)
        zip_path.unlink()
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        tmp.replace(path)
        log(f"{stamp}: {len(data) / 1e6:.1f} MB, {len(rows)} Indonesian rows")
    return missing, downloaded


def merge(out, keywords, start, end, missing=(), downloaded=0):
    def rows():
        for stamp in stamps(start, end):
            path = Path(out) / "parts" / f"{stamp}.jsonl"
            if path.exists():
                yield from (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())

    return write_snapshot(out, rows(), start, end, {
        "source": "GDELT 2.0 translingual GKG raw files (https://www.gdeltproject.org/)",
        "citation": "Data from the GDELT Project, https://www.gdeltproject.org/",
        "language": "Indonesian (srclc:ind)", "keywords": list(keywords),
        "missing_files": list(missing), "bytes_downloaded": downloaded})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start", required=True, type=date.fromisoformat, help="first WIB day, YYYY-MM-DD")
    ap.add_argument("--end", required=True, type=date.fromisoformat, help="last WIB day, YYYY-MM-DD")
    ap.add_argument("--keywords", nargs="*", default=[])
    ap.add_argument("--out", required=True)
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between downloads (at least 1)")
    args = ap.parse_args(argv)
    if args.pause < 1:
        ap.error("--pause must be at least 1 second")
    started = time.monotonic()
    todo = list(stamps(args.start, args.end))
    missing, downloaded = collect(todo, args.keywords, args.out, pause=args.pause)
    m = merge(args.out, args.keywords, args.start, args.end, missing, downloaded)
    print(f"{m['rows_unique']} unique rows, {m['duplicates_removed']} duplicates removed, "
          f"{m['skipped']} skipped, {len(missing)} files missing, {downloaded / 1e6:.0f} MB downloaded, "
          f"{time.monotonic() - started:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
