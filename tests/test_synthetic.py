import importlib.util
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from slmcp.adapters.csv_json import FileAdapter

SCRIPT = Path(__file__).parent.parent / "scripts" / "make_synthetic.py"
spec = importlib.util.spec_from_file_location("make_synthetic", SCRIPT)
make_synthetic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_synthetic)
START = date(2026, 8, 1)


def test_same_seed_gives_the_same_file(tmp_path):
    _, a = make_synthetic.make_dataset(tmp_path / "a.jsonl")
    _, b = make_synthetic.make_dataset(tmp_path / "b.jsonl")
    _, c = make_synthetic.make_dataset(tmp_path / "c.jsonl", seed=12)
    assert a == b and a != c


def test_dataset_loads_cleanly_with_spikes_empty_day_and_boundaries(tmp_path):
    path, _ = make_synthetic.make_dataset(tmp_path / "posts.jsonl")
    adapter = FileAdapter(path)
    assert adapter.skipped == 0 and len(adapter.posts) == 2000
    per_day = Counter(p.date_wib.date() for p in adapter.posts)
    assert per_day[START + timedelta(days=make_synthetic.EMPTY_DAY)] == 0
    normal = sorted(per_day.values())[len(per_day) // 2]
    for _, day, extra in make_synthetic.SPIKES:
        assert per_day[START + timedelta(days=day)] >= max(extra, 2 * normal)
    day5, day6 = START + timedelta(days=5), START + timedelta(days=6)
    times = {(p.date_wib.date(), p.date_wib.strftime("%H:%M:%S")) for p in adapter.posts}
    assert {(day5, "23:30:00"), (day5, "23:59:59"), (day6, "00:10:00"), (day6, "00:00:00")} <= times


def test_dataset_text_and_links_are_invented(tmp_path):
    path, _ = make_synthetic.make_dataset(tmp_path / "posts.jsonl")
    posts = FileAdapter(path).posts
    assert all(p.url.startswith("https://example.com/") for p in posts)
    assert {p.source for p in posts if p.platform == "news"} == {"Kabar Kota Contoh"}
