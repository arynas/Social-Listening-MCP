"""Build a frozen snapshot of public news metadata from the GDELT DOC 2.0 API.

Only metadata that GDELT itself publishes is stored: headline, URL, domain, time, language. No article
bodies are downloaded. GDELT terms (https://www.gdeltproject.org/about.html): free, unrestricted use and
redistribution, with a citation to the GDELT Project and a link to https://www.gdeltproject.org/.

    python scripts/build_snapshot.py --start 2026-10-01 --end 2026-10-07 \\
        --keywords banjir "harga beras" gempa --out data/snapshot_pilot

Resumable: each (keyword, day) result is saved under <out>/parts/ and skipped on the next run.
Polite: one request at a time, a pause between requests (default 6 s; GDELT asks for at most one per
5 s), and a long back-off on HTTP 429. Note: the DOC API only searches the last 3 months.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from slmcp import __version__  # noqa: E402
from slmcp.adapters.base import utc_window  # noqa: E402
from slmcp.snapshot import write_snapshot  # noqa: E402

API = "https://api.gdeltproject.org/api/v2/doc/doc"
USER_AGENT = f"social-listening-mcp/{__version__} (snapshot builder)"
MAX_RECORDS = 250
MIN_WINDOW = timedelta(hours=1)


class Blocked(RuntimeError):
    """GDELT keeps answering 429 (or fails) after all retries; stop and resume later."""


def http_get(url, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


class Client:
    """Calls the DOC API one request at a time with pauses. get and sleep can be replaced in tests."""

    def __init__(self, pause=6.0, backoff=(60, 120, 300), lang="indonesian", get=http_get, sleep=time.sleep):
        self.pause, self.backoff, self.lang = pause, backoff, lang
        self.get, self.sleep = get, sleep
        self.calls, self.last = 0, None

    def url(self, keyword, begin, end):
        term = f'"{keyword}"' if " " in keyword else keyword
        query = f"{term} sourcelang:{self.lang}"
        params = {"query": query, "mode": "artlist", "format": "json", "maxrecords": MAX_RECORDS,
                  "sort": "datedesc", "startdatetime": begin.strftime("%Y%m%d%H%M%S"),
                  "enddatetime": end.strftime("%Y%m%d%H%M%S")}
        return f"{API}?{urllib.parse.urlencode(params)}"

    def articles(self, keyword, begin, end):
        url = self.url(keyword, begin, end)
        for wait in (0, *self.backoff):
            if wait:
                self.sleep(wait)
            if self.last is not None:
                self.sleep(max(0.0, self.pause - (time.monotonic() - self.last)))
            self.calls += 1
            try:
                body = self.get(url)
            except urllib.error.HTTPError as e:
                self.last = time.monotonic()
                if e.code == 429 or e.code >= 500:
                    continue
                raise
            except (urllib.error.URLError, TimeoutError):
                self.last = time.monotonic()
                continue
            self.last = time.monotonic()
            if body.lstrip().startswith("Please limit requests"):
                continue
            return parse_response(body)
        raise Blocked(f"GDELT did not answer for {keyword!r} {begin:%Y-%m-%d %H:%M}..{end:%H:%M} UTC; "
                      "run the same command again later to resume")


def parse_response(body):
    """The DOC API JSON (artlist) -> list of article dicts. An empty result can be '{}' or empty."""
    body = body.strip()
    if not body:
        return []
    data = json.loads(body, strict=False)
    return data.get("articles", []) if isinstance(data, dict) else []


def to_row(article, keyword):
    """One GDELT article -> a row for the Post schema (text = headline, source = domain)."""
    seen = article.get("seendate", "")
    when = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).isoformat() if seen else ""
    return {"date": when, "text": article.get("title", ""), "source": article.get("domain", ""),
            "platform": "news", "url": article.get("url", ""), "language": article.get("language", ""),
            "keywords": [keyword]}


def fetch_window(client, keyword, begin, end):
    """All articles in [begin, end). If GDELT returns the maximum, split the window in two so nothing is
    cut off (down to 1 hour; a full 1-hour window is reported as possibly incomplete)."""
    found = client.articles(keyword, begin, end)
    if len(found) < MAX_RECORDS or end - begin <= MIN_WINDOW:
        return found, ([] if len(found) < MAX_RECORDS else [f"{keyword} {begin:%Y-%m-%d %H:%M} UTC"])
    middle = begin + (end - begin) / 2
    a, full_a = fetch_window(client, keyword, begin, middle)
    b, full_b = fetch_window(client, keyword, middle, end)
    return a + b, full_a + full_b


def days(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def collect(client, keywords, start, end, out, log=print):
    """Fetch every (keyword, WIB day) that isn't saved yet. Returns the list of possibly cut-off windows."""
    parts = Path(out) / "parts"
    full = []
    for day in days(start, end):
        for keyword in keywords:
            path = parts / day.isoformat() / f"{slug(keyword)}.jsonl"
            if path.exists():
                continue
            begin, stop = utc_window(day, day)
            articles, cut = fetch_window(client, keyword, begin, stop)
            full += cut
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(to_row(a, keyword), ensure_ascii=False) + "\n"
                                   for a in articles), encoding="utf-8")
            tmp.replace(path)
            log(f"{day} {keyword}: {len(articles)} articles")
    return full


def slug(keyword):
    return "".join(c if c.isalnum() else "_" for c in keyword.lower()).strip("_")


def merge(out, keywords, start, end, cut_off=()):
    """Join the saved parts into one JSONL per month (deduplicated by URL, keywords merged), keep only
    rows that fit the schema, and write manifest.json. Returns the manifest."""
    def rows():
        for day in days(start, end):
            for keyword in keywords:
                path = Path(out) / "parts" / day.isoformat() / f"{slug(keyword)}.jsonl"
                if path.exists():
                    yield from (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())

    return write_snapshot(out, rows(), start, end, {
        "source": "GDELT DOC 2.0 API (https://www.gdeltproject.org/)",
        "citation": "Data from the GDELT Project, https://www.gdeltproject.org/",
        "keywords": list(keywords), "possibly_cut_off_windows": list(cut_off)})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--start", required=True, type=date.fromisoformat, help="first WIB day, YYYY-MM-DD")
    ap.add_argument("--end", required=True, type=date.fromisoformat, help="last WIB day, YYYY-MM-DD")
    ap.add_argument("--keywords", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pause", type=float, default=6.0, help="seconds between requests (at least 5)")
    ap.add_argument("--lang", default="indonesian", help="GDELT sourcelang value")
    args = ap.parse_args(argv)
    if args.pause < 5:
        ap.error("--pause must be at least 5 seconds (GDELT's limit)")
    client = Client(pause=args.pause, lang=args.lang)
    started = time.monotonic()
    try:
        cut = collect(client, args.keywords, args.start, args.end, args.out)
    except Blocked as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 2
    manifest = merge(args.out, args.keywords, args.start, args.end, cut)
    print(f"{manifest['rows_unique']} unique rows, {manifest['duplicates_removed']} duplicates removed, "
          f"{manifest['skipped']} skipped, {client.calls} requests, {time.monotonic() - started:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
