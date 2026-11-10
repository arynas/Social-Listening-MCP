"""Label the sentiment of news headlines with an OpenAI-style chat model (e.g. Qwen3.6-27B on llama.cpp).

    python scripts/label_sentiment.py --input data/snapshot/posts-2026-10.jsonl \\
        --labels data/snapshot/labels.jsonl --endpoint http://localhost:8000/v1 --model Qwen3.6-27B

Labels are positif / negatif / netral (stored in the Post schema as positive / negative / neutral).
Headlines go in batches (default 20) with temperature 0 and thinking off; every answer must be strict JSON
with one valid label per headline. A bad batch is retried once, then its rows are recorded as unlabelled.
Labels go to a separate file keyed by URL (resumable: URLs already there are skipped). --write-labelled
writes <input>.labelled.jsonl with the sentiment field filled and records its sha256 in manifest.json.
The original snapshot file is never changed. A key, if the endpoint needs one, is read from the
environment variable named by --key-env.
"""
import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

PROMPT_VERSION = "v1"
LABELS = {"positif": "positive", "negatif": "negative", "netral": "neutral"}
SYSTEM = (
    "Kamu memberi label sentimen untuk judul berita berbahasa Indonesia. Untuk setiap judul, pilih satu "
    "label: positif (kabar baik, kemajuan, bantuan, keberhasilan), negatif (bencana, kecelakaan, "
    "kejahatan, konflik, kerugian, keluhan), atau netral (informasi biasa, jadwal, pengumuman, tidak jelas "
    "baik atau buruk). Nilai isi beritanya, bukan gaya bahasanya. Jawab HANYA dengan JSON: "
    '{"labels": [{"i": 1, "label": "netral"}, ...]} dengan satu entri untuk setiap nomor judul.'
)


class BadAnswer(ValueError):
    pass


def http_chat(endpoint, model, messages, key=None, timeout=300):
    body = {"model": model, "messages": messages, "temperature": 0, "max_tokens": 2000,
            "chat_template_kwargs": {"enable_thinking": False}}
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(f"{endpoint.rstrip('/')}/chat/completions",
                                     data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read())
    return data["choices"][0]["message"]["content"]


def prompt(titles):
    lines = "\n".join(f"{i}. {t}" for i, t in enumerate(titles, start=1))
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"Judul:\n{lines}"}]


def parse_answer(text, n):
    """The model's answer -> list of n labels (positif/negatif/netral) in order, or BadAnswer."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise BadAnswer(f"not JSON ({e.msg})") from None
    items = data.get("labels") if isinstance(data, dict) else None
    if not isinstance(items, list) or len(items) != n:
        raise BadAnswer(f"expected {n} labels, got {len(items) if isinstance(items, list) else 'none'}")
    labels = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("i"), int):
            raise BadAnswer(f"bad entry {item!r}")
        label = str(item.get("label", "")).strip().lower()
        if label not in LABELS:
            raise BadAnswer(f"unknown label {label!r}")
        labels[item["i"]] = label
    if sorted(labels) != list(range(1, n + 1)):
        raise BadAnswer("numbers don't match the headlines")
    return [labels[i] for i in range(1, n + 1)]


def load_posts(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def done_urls(labels_path):
    path = Path(labels_path)
    if not path.exists():
        return set()
    return {json.loads(line)["url"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def label_rows(rows, labels_path, chat, model, batch=20, log=print):
    """Label every row whose URL isn't in labels_path yet; append results. Returns counts."""
    todo = [r for r in rows if r["url"] not in done_urls(labels_path)]
    counts = {"batches": 0, "labelled": 0, "unlabelled": 0, "retries": 0}
    with open(labels_path, "a", encoding="utf-8") as out:
        for start in range(0, len(todo), batch):
            chunk = todo[start:start + batch]
            labels, error = None, None
            for attempt in (1, 2):
                try:
                    labels = parse_answer(chat(prompt([r["text"] for r in chunk])), len(chunk))
                    break
                except (BadAnswer, OSError, KeyError, ValueError) as e:
                    error = f"{type(e).__name__}: {e}"
                    if attempt == 1:
                        counts["retries"] += 1
            stamp = datetime.now(UTC).isoformat(timespec="seconds")
            for i, row in enumerate(chunk):
                rec = {"url": row["url"], "label": labels[i] if labels else None, "model": model,
                       "prompt_version": PROMPT_VERSION, "labelled_at": stamp}
                if not labels:
                    rec["error"] = error
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            counts["batches"] += 1
            counts["labelled" if labels else "unlabelled"] += len(chunk)
            log(f"batch {counts['batches']}: {len(chunk)} rows, {'ok' if labels else 'unlabelled: ' + error}")
    return counts


def write_labelled(input_path, labels_path, manifest_path=None):
    """Write <input>.labelled.jsonl (sentiment filled from the labels file) and record it in the manifest."""
    labels = {}
    for line in Path(labels_path).read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("label"):
            labels[rec["url"]] = rec
    src = Path(input_path)
    out = src.with_name(src.stem + ".labelled.jsonl")
    rows, n_labelled, models, prompts = load_posts(src), 0, set(), set()
    with open(out, "w", encoding="utf-8") as f:
        for row in rows:
            rec = labels.get(row["url"])
            if rec:
                row = {**row, "sentiment": LABELS[rec["label"]]}
                n_labelled += 1
                models.add(rec["model"])
                prompts.add(rec["prompt_version"])
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    info = {"rows": len(rows), "labelled": n_labelled, "unlabelled": len(rows) - n_labelled,
            "sha256": hashlib.sha256(out.read_bytes()).hexdigest(), "model": sorted(models),
            "prompt_version": sorted(prompts), "source_file": src.name}
    manifest_path = Path(manifest_path or src.with_name("manifest.json"))
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.setdefault("labelled_files", {})[out.name] = info
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return out, info


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--endpoint", default="http://localhost:8000/v1")
    ap.add_argument("--model", default="Qwen3.6-27B")
    ap.add_argument("--key-env", help="name of the environment variable that holds the key, if any")
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--sample", type=int, help="label only N rows chosen with --seed (pilot)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--write-labelled", action="store_true")
    args = ap.parse_args(argv)
    key = os.environ.get(args.key_env) if args.key_env else None
    rows = load_posts(args.input)
    if args.sample:
        rows = random.Random(args.seed).sample(rows, args.sample)
    started = time.monotonic()
    counts = label_rows(rows, args.labels, lambda m: http_chat(args.endpoint, args.model, m, key), args.model,
                        batch=args.batch)
    print(f"{counts}, {time.monotonic() - started:.0f} s", flush=True)
    if args.write_labelled:
        out, info = write_labelled(args.input, args.labels)
        print(f"wrote {out.name}: {info['labelled']} labelled, {info['unlabelled']} unlabelled", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
