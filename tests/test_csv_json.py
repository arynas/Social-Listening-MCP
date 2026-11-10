import csv
import json
from pathlib import Path

import pytest
from adapter_contract import AdapterContract

from slmcp.adapters.csv_json import FileAdapter
from slmcp.schema import WIB

FIELDS = ["date", "text", "source", "platform", "url", "author", "engagement", "sentiment"]
EXAMPLE = Path(__file__).parent.parent / "examples" / "posts_sample.csv"


def write_csv(path, rows, fields=FIELDS, bom=False):
    with open(path, "w", encoding="utf-8-sig" if bom else "utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return path


def as_rows(posts):
    return [{k: ("" if v is None else v) for k, v in p.to_dict().items()} for p in posts]


class TestCsvAdapter(AdapterContract):
    def make(self, posts):
        path = Path(self.tmp) / "posts.csv"
        return FileAdapter(write_csv(path, as_rows(posts)))

    @pytest.fixture(autouse=True)
    def _tmp(self, tmp_path):
        self.tmp = tmp_path


class TestJsonlAdapter(AdapterContract):
    def make(self, posts):
        path = Path(self.tmp) / "posts.jsonl"
        path.write_text("".join(json.dumps(p.to_dict()) + "\n" for p in posts), encoding="utf-8")
        return FileAdapter(path)

    @pytest.fixture(autouse=True)
    def _tmp(self, tmp_path):
        self.tmp = tmp_path


ROW = {"date": "2026-11-09T08:00:00+07:00", "text": "Jalur sepeda baru", "source": "Media Contoh",
       "platform": "news", "url": "https://example.com/1", "author": "", "engagement": "",
       "sentiment": ""}


def test_column_mapping_and_defaults(tmp_path):
    path = tmp_path / "other.csv"
    write_csv(path, [{"created_at": "2026-11-09 08:00", "content": "Jalur sepeda baru",
                      "link": "https://example.com/1"}], fields=["created_at", "content", "link"])
    adapter = FileAdapter(path, columns={"created_at": "date", "content": "text", "link": "url"},
                          assume_tz=WIB, defaults={"platform": "news", "source": "Media Contoh"})
    assert adapter.skipped == 0
    [post] = adapter.posts
    assert (post.platform, post.source, post.date_wib.hour) == ("news", "Media Contoh", 8)


def test_bad_rows_are_skipped_and_reported(tmp_path):
    rows = [ROW, {**ROW, "date": "kemarin"}, {**ROW, "platform": "myspace"}, {**ROW, "url": ""}]
    adapter = FileAdapter(write_csv(tmp_path / "p.csv", rows))
    assert len(adapter.posts) == 1
    assert adapter.skipped == 3
    errors = adapter.first_errors(2)
    assert errors[0].startswith("row 3: date") and errors[1].startswith("row 4: platform")


def test_bad_jsonl_lines_are_skipped_and_reported(tmp_path):
    path = tmp_path / "p.jsonl"
    path.write_text(json.dumps(ROW) + "\n{not json\n[1, 2]\n\n", encoding="utf-8")
    adapter = FileAdapter(path)
    assert len(adapter.posts) == 1
    assert [e.split(":")[0] for e in adapter.errors] == ["row 2", "row 3"]


def test_utf8_bom_is_read(tmp_path):
    adapter = FileAdapter(write_csv(tmp_path / "bom.csv", [ROW], bom=True))
    assert adapter.skipped == 0 and len(adapter.posts) == 1


@pytest.mark.parametrize("content", ["", "date,text,source,platform,url\n"])
def test_empty_file_gives_no_posts_and_no_errors(tmp_path, content):
    path = tmp_path / "empty.csv"
    path.write_text(content, encoding="utf-8")
    adapter = FileAdapter(path)
    assert adapter.posts == [] and adapter.skipped == 0


def test_unknown_file_type_is_refused(tmp_path):
    path = tmp_path / "posts.xlsx"
    path.write_text("x")
    with pytest.raises(ValueError, match="csv or .jsonl"):
        FileAdapter(path)


def test_example_csv_loads_cleanly():
    adapter = FileAdapter(EXAMPLE)
    assert adapter.skipped == 0
    assert 50 <= len(adapter.posts) <= 100
    assert all(p.url.startswith("https://example.com/") for p in adapter.posts)
