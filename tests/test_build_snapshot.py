import importlib.util
import json
import urllib.error
import urllib.parse
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from slmcp.adapters.csv_json import FileAdapter

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("build_snapshot", ROOT / "scripts" / "build_snapshot.py")
bs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bs)
FIXTURE = (ROOT / "tests" / "fixtures" / "gdelt_artlist.json").read_text()


def client(get, **kw):
    return bs.Client(pause=0, backoff=(1, 1), get=get, sleep=lambda s: None, **kw)


def test_parse_response_and_row_mapping():
    articles = bs.parse_response(FIXTURE)
    assert len(articles) == 3
    row = bs.to_row(articles[0], "banjir")
    assert row == {"date": "2026-10-01T01:30:00+00:00", "text": "Banjir rendam jalan utama kota contoh",
                   "source": "example.com", "platform": "news", "url": "https://example.com/berita/banjir-1",
                   "language": "Indonesian", "keywords": ["banjir"]}
    assert bs.parse_response("") == [] and bs.parse_response("{}") == []


def test_query_url_uses_language_phrase_and_window():
    c = client(lambda u: "")
    url = c.url("harga beras", datetime(2026, 9, 30, 17, tzinfo=UTC), datetime(2026, 10, 1, 17, tzinfo=UTC))
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["query"] == ['"harga beras" sourcelang:indonesian']
    assert q["startdatetime"] == ["20260930170000"] and q["enddatetime"] == ["20261001170000"]
    assert q["maxrecords"] == ["250"] and q["mode"] == ["artlist"]


def test_429_is_retried_then_blocked():
    calls = []

    def get(url):
        calls.append(url)
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)

    with pytest.raises(bs.Blocked, match="resume"):
        client(get).articles("banjir", datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 2, tzinfo=UTC))
    assert len(calls) == 3  # first try + 2 back-offs


def test_rate_limit_text_is_treated_like_429():
    answers = iter(["Please limit requests to one every 5 seconds", FIXTURE])
    found = client(lambda u: next(answers)).articles("banjir", datetime(2026, 10, 1, tzinfo=UTC),
                                                     datetime(2026, 10, 2, tzinfo=UTC))
    assert len(found) == 3


def test_full_window_is_split_so_nothing_is_cut_off():
    def get(url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        begin, end = (datetime.strptime(q[k][0], "%Y%m%d%H%M%S") for k in ("startdatetime", "enddatetime"))
        # the whole day returns the maximum (250); each 12-hour half returns 3
        n = 250 if (end - begin).total_seconds() == 86400 else 3
        return json.dumps({"articles": [{"url": f"https://example.com/{begin:%H}/{i}", "title": "x",
                                         "seendate": "20261001T020000Z", "domain": "example.com"}
                                        for i in range(n)]})

    c = client(get)
    found, cut = bs.fetch_window(c, "banjir", datetime(2026, 9, 30, 17, tzinfo=UTC),
                                 datetime(2026, 10, 1, 17, tzinfo=UTC))
    assert c.calls == 3 and len(found) == 6 and cut == []


def test_collect_merge_resume_and_manifest(tmp_path):
    c = client(lambda u: FIXTURE)
    logs = []
    bs.collect(c, ["banjir", "lumpur"], date(2026, 10, 1), date(2026, 10, 2), tmp_path, log=logs.append)
    assert c.calls == 4 and len(logs) == 4
    # resume: nothing is fetched again
    bs.collect(c, ["banjir", "lumpur"], date(2026, 10, 1), date(2026, 10, 2), tmp_path)
    assert c.calls == 4
    m = bs.merge(tmp_path, ["banjir", "lumpur"], date(2026, 10, 1), date(2026, 10, 2))
    # each call returned the same 3 articles: 12 fetched, 4 without a date skipped, 2 unique kept
    assert (m["rows_fetched"], m["skipped"], m["rows_unique"], m["duplicates_removed"]) == (12, 4, 2, 6)
    # 16:30 UTC on 1 Oct is 23:30 WIB on 1 Oct
    assert m["rows_per_day"] == {"2026-10-01": 2}
    assert m["rows_per_keyword_fetched"] == {"banjir": 6, "lumpur": 6}
    [name] = m["files"]
    rows = [json.loads(line) for line in (tmp_path / name).read_text().splitlines()]
    assert all(r["keywords"] == ["banjir", "lumpur"] for r in rows)
    adapter = FileAdapter(tmp_path / name)
    assert adapter.skipped == 0 and len(adapter.posts) == 2
    assert json.loads((tmp_path / "manifest.json").read_text())["files"][name]["rows"] == 2


def test_pause_below_five_seconds_is_refused():
    with pytest.raises(SystemExit):
        bs.main(["--start", "2026-10-01", "--end", "2026-10-01", "--keywords", "x", "--out", "/tmp/x",
                 "--pause", "1"])
