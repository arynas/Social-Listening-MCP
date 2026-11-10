import csv
import importlib.util
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("hc", ROOT / "scripts" / "handcheck.py")
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)


def labelled(tmp_path, n=300):
    sentiments = ["positive", "negative", "neutral"]
    rows = [{"date": f"2026-10-{1 + i % 14:02d}T0{i % 10}:00:00+00:00", "text": f"Judul {i}",
             "source": "example.com", "platform": "news", "url": f"https://example.com/{i}",
             **({"sentiment": sentiments[i % 3]} if i % 10 else {})} for i in range(n)]
    path = tmp_path / "posts.labelled.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_make_writes_a_blind_sheet_and_a_separate_key(tmp_path):
    sheet, key, mix = hc.make(labelled(tmp_path), tmp_path / "out")
    assert sum(mix.values()) == 100 and max(mix.values()) - min(mix.values()) <= 1
    with open(sheet, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == ["no", "tanggal", "sumber", "judul", "label_ari"]
    assert len(rows) == 100 and all(r["label_ari"] == "" for r in rows)
    assert len({r["tanggal"][:10] for r in rows}) >= 10  # spread across days
    with open(key, encoding="utf-8-sig") as f:
        keys = list(csv.DictReader(f))
    assert [k["no"] for k in keys] == [r["no"] for r in rows]
    assert "label_model" not in rows[0]


def test_make_is_repeatable_with_the_same_seed(tmp_path):
    a = hc.make(labelled(tmp_path), tmp_path / "a")[0].read_bytes()
    b = hc.make(labelled(tmp_path), tmp_path / "b")[0].read_bytes()
    assert a == b


def test_compare_counts_matches_blanks_aliases_and_unknown(tmp_path):
    sheet, key = tmp_path / "s.csv", tmp_path / "k.csv"
    sheet.write_text("no,tanggal,sumber,judul,label_ari\n1,,,,positif\n2,,,,Neg\n3,,,,\n4,,,,senang\n5,,,,netral\n",
                     encoding="utf-8-sig")
    key.write_text("no,label_model,url\n1,positif,u\n2,netral,u\n3,netral,u\n4,negatif,u\n5,netral,u\n",
                   encoding="utf-8-sig")
    matched, checked, confusion, blanks, unknown = hc.compare(sheet, key)
    assert (matched, checked, blanks, unknown) == (2, 3, ["3"], [("4", "senang")])
    assert confusion == Counter({("positif", "positif"): 1, ("netral", "negatif"): 1,
                                 ("netral", "netral"): 1})
