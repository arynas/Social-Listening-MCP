import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("ls", ROOT / "scripts" / "label_sentiment.py")
ls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ls)


def rows(n):
    return [{"date": "2026-10-01T01:00:00+00:00", "text": f"Judul contoh {i}", "source": "example.com",
             "platform": "news", "url": f"https://example.com/{i}", "keywords": []} for i in range(n)]


def answer(labels):
    return json.dumps({"labels": [{"i": i, "label": lab} for i, lab in enumerate(labels, start=1)]})


def test_parse_answer_accepts_the_v2_plain_list():
    assert ls.parse_answer(json.dumps({"labels": ["Positif", "netral"]}), 2) == ["positif", "netral"]


def test_parse_answer_accepts_json_and_code_fences():
    assert ls.parse_answer(answer(["positif", "Negatif"]), 2) == ["positif", "negatif"]
    assert ls.parse_answer("```json\n" + answer(["netral"]) + "\n```", 1) == ["netral"]


@pytest.mark.parametrize("text, match", [
    ("bukan json", "not JSON"),
    (answer(["positif"]), "expected 2"),
    (answer(["positif", "senang"]), "unknown label"),
    (json.dumps({"labels": [{"i": 1, "label": "netral"}, {"i": 1, "label": "netral"}]}), "numbers"),
])
def test_parse_answer_refuses_bad_answers(text, match):
    with pytest.raises(ls.BadAnswer, match=match):
        ls.parse_answer(text, 2)


def test_prompt_numbers_the_headlines_and_asks_for_json():
    messages = ls.prompt(["A", "B"])
    assert messages[1]["content"] == "Judul:\n1. A\n2. B"
    assert "JSON" in messages[0]["content"]


def test_label_rows_batches_retries_resumes_and_marks_unlabelled(tmp_path):
    labels_path = tmp_path / "labels.jsonl"
    calls = []

    def chat(messages):
        calls.append(messages)
        n = messages[1]["content"].count("\n")
        if len(calls) in (2, 3):  # batch 2 fails twice -> unlabelled
            return "maaf"
        if len(calls) == 4:  # batch 3 fails once, then works
            return "{}"
        return answer(["positif"] * n)

    counts = ls.label_rows(rows(25), labels_path, chat, "m", batch=10, log=lambda s: None)
    assert counts == {"batches": 3, "labelled": 15, "unlabelled": 10, "retries": 2}
    recs = [json.loads(line) for line in labels_path.read_text().splitlines()]
    assert len(recs) == 25
    assert all(r["model"] == "m" and r["prompt_version"] == ls.PROMPT_VERSION for r in recs)
    missing = [r for r in recs if r["label"] is None]
    assert len(missing) == 10 and all("error" in r for r in missing)
    # resume: every URL already has a record, so no new calls
    before = len(calls)
    ls.label_rows(rows(25), labels_path, chat, "m", batch=10, log=lambda s: None)
    assert len(calls) == before


def test_write_labelled_keeps_original_and_records_sha(tmp_path):
    src = tmp_path / "posts-2026-10.jsonl"
    src.write_text("".join(json.dumps(r) + "\n" for r in rows(3)), encoding="utf-8")
    original = src.read_bytes()
    (tmp_path / "manifest.json").write_text(json.dumps({"files": {}}))
    labels = tmp_path / "labels.jsonl"
    labels.write_text("".join(json.dumps(r) + "\n" for r in [
        {"url": "https://example.com/0", "label": "negatif", "model": "m", "prompt_version": "v1"},
        {"url": "https://example.com/1", "label": None, "model": "m", "prompt_version": "v1"}]))
    out, info = ls.write_labelled(src, labels)
    assert src.read_bytes() == original
    out_rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert out_rows[0]["sentiment"] == "negative" and "sentiment" not in out_rows[1]
    assert (info["labelled"], info["unlabelled"]) == (1, 2)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["labelled_files"]["posts-2026-10.labelled.jsonl"]["sha256"] == info["sha256"]
