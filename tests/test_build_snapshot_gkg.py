import importlib.util
import json
import urllib.error
from datetime import date
from pathlib import Path

from slmcp.adapters.csv_json import FileAdapter

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("gkg", ROOT / "scripts" / "build_snapshot_gkg.py")
gkg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gkg)
FIXTURE = (ROOT / "tests" / "fixtures" / "gkg_translation_sample.zip").read_bytes()
KEYWORDS = ["banjir", "harga beras", "gempa"]


def test_stamps_cover_one_wib_day():
    s = list(gkg.stamps(date(2026, 10, 1), date(2026, 10, 1)))
    assert len(s) == 96
    assert s[0] == "20260930170000" and s[-1] == "20261001164500"


def test_parse_keeps_indonesian_rows_with_a_title_only():
    rows = gkg.parse_gkg(FIXTURE, KEYWORDS)
    # 6 records: 1 Spanish dropped; the one without a title is kept here and skipped later by the schema
    assert len(rows) == 5
    first = rows[0]
    assert first == {"date": "2026-10-01T00:30:00+00:00", "text": "Banjir Rendam Jalan Utama & Pasar",
                     "source": "example.com", "platform": "news", "url": "https://example.com/a/banjir",
                     "language": "ind", "keywords": ["banjir"]}
    assert rows[1]["text"] == "Harga beras stabil di pasar contoh"
    assert rows[1]["keywords"] == ["harga beras"]
    assert rows[2]["keywords"] == []


def test_collect_resume_missing_files_and_manifest(tmp_path):
    calls, logs = [], []

    def get(url):
        calls.append(url)
        if url.endswith("20260930171500.translation.gkg.csv.zip"):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return FIXTURE

    todo = ["20260930170000", "20260930171500", "20260930173000"]
    missing, size = gkg.collect(todo, KEYWORDS, tmp_path, get=get, sleep=lambda s: None, log=logs.append)
    assert missing == ["20260930171500"] and size == 2 * len(FIXTURE) and len(calls) == 3
    assert not list((tmp_path / "parts").glob("*.zip"))  # zips are deleted after parsing
    # resume: nothing is downloaded again
    gkg.collect(todo, KEYWORDS, tmp_path, get=get, sleep=lambda s: None)
    assert len(calls) == 3
    m = gkg.merge(tmp_path, KEYWORDS, date(2026, 10, 1), date(2026, 10, 1), missing, size)
    # 2 files x 5 rows = 10 fetched; 2 without a title skipped; 3 unique URLs; 5 duplicates
    assert (m["rows_fetched"], m["skipped"], m["rows_unique"], m["duplicates_removed"]) == (10, 2, 3, 5)
    assert m["rows_per_day"] == {"2026-10-01": 3}
    assert m["rows_per_keyword_unique"] == {"banjir": 1, "harga beras": 1}
    assert m["missing_files"] == ["20260930171500"]
    [name] = m["files"]
    adapter = FileAdapter(tmp_path / name)
    assert adapter.skipped == 0 and len(adapter.posts) == 3
    rows = [json.loads(line) for line in (tmp_path / name).read_text().splitlines()]
    assert {r["source"] for r in rows} == {"example.com", "example.org", "example.net"}
