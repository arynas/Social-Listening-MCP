"""Read posts from a local CSV or JSONL file into the shared schema."""
import csv
import json
from pathlib import Path

from slmcp.adapters.base import Adapter, filter_posts
from slmcp.schema import PostError, from_dict


class FileAdapter(Adapter):
    """Loads a .csv or .jsonl file once, then searches it in memory.

    columns: maps the file's column names to schema fields, for files that use other names,
        e.g. {"created_at": "date", "content": "text"}. Other columns keep their own name.
    assume_tz: used for dates written without a time zone (e.g. WIB for local times).
    defaults: values for fields the file lacks or leaves empty, e.g. {"platform": "news"}.
    Rows that don't fit the schema are skipped, never silently: see skipped and errors.
    """

    def __init__(self, path, columns=None, assume_tz=None, defaults=None, max_days=None):
        self.path = Path(path)
        self.columns = dict(columns or {})
        self.assume_tz = assume_tz
        self.defaults = dict(defaults or {})
        if max_days is not None:
            self.max_days = max_days
        self.posts, self.errors = [], []
        for row_no, raw in self._rows():
            try:
                self.posts.append(from_dict(self._map(raw), assume_tz=self.assume_tz))
            except PostError as e:
                self.errors.append(f"row {row_no}: {e}")

    @property
    def skipped(self):
        return len(self.errors)

    def first_errors(self, n=5):
        return self.errors[:n]

    def _rows(self):
        suffix = self.path.suffix.lower()
        if suffix not in (".csv", ".jsonl", ".ndjson"):
            raise ValueError(f"{self.path.name}: use a .csv or .jsonl file")
        # utf-8-sig also reads files saved with a byte-order mark (common from Excel)
        with self.path.open(encoding="utf-8-sig", newline="") as f:
            if suffix == ".csv":
                # row numbers count lines in the file; line 1 is the header
                yield from enumerate(csv.DictReader(f), start=2)
                return
            for row_no, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as e:
                    self.errors.append(f"row {row_no}: not valid JSON ({e.msg})")
                    continue
                if not isinstance(row, dict):
                    self.errors.append(f"row {row_no}: not a JSON object")
                    continue
                yield row_no, row

    def _map(self, raw):
        row = {self.columns.get(k, k): v for k, v in raw.items()}
        for field, value in self.defaults.items():
            if row.get(field) in (None, ""):
                row[field] = value
        return row

    def _search(self, keyword, start, end):
        return filter_posts(self.posts, keyword, start, end)
