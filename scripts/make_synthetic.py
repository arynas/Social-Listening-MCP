"""Synthetic posts for demos and tests. All text, names and links are invented; a fixed seed gives the
same output every run.

    python scripts/make_synthetic.py            # examples/posts_sample.csv (80 rows, committed)
    python scripts/make_synthetic.py dataset    # data/synthetic/posts.jsonl (~2,000 posts, git-ignored)
"""
import csv
import hashlib
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

from slmcp.schema import WIB

TOPICS = {
    "jalur sepeda": ["Jalur sepeda baru dibuka di pusat kota.", "Jalur sepeda sering dipakai parkir motor.",
                     "Warga minta jalur sepeda diperpanjang.", "Lampu di jalur sepeda banyak yang mati."],
    "harga cabai": ["Harga cabai naik lagi di pasar pagi ini.", "Pedagang bilang harga cabai mulai turun.",
                    "Harga cabai bikin warung makan menaikkan harga."],
    "banjir": ["Banjir setinggi lutut di jalan utama setelah hujan deras.", "Relawan membagikan makanan "
               "untuk warga terdampak banjir.", "Saluran air dibersihkan untuk mencegah banjir."],
}
SENTIMENT = {"baru dibuka": "positive", "diperpanjang": "neutral", "mati": "negative", "naik": "negative",
             "turun": "positive", "menaikkan": "negative", "Banjir setinggi": "negative",
             "Relawan": "positive", "dibersihkan": "positive", "parkir motor": "negative"}
PLATFORMS = ["news", "twitter", "twitter", "instagram", "tiktok", "youtube", "facebook"]


def main(path=Path(__file__).parent.parent / "examples" / "posts_sample.csv", n=80, seed=7):
    rng = random.Random(seed)
    start = datetime(2026, 11, 1, tzinfo=WIB)
    rows = []
    for i in range(n):
        topic = rng.choice(list(TOPICS))
        text = rng.choice(TOPICS[topic])
        platform = rng.choice(PLATFORMS)
        when = start + timedelta(minutes=rng.randrange(9 * 24 * 60))
        sentiment = next((s for k, s in SENTIMENT.items() if k in text), "neutral")
        source = "Kabar Kota Contoh" if platform == "news" else f"@akun_contoh_{rng.randrange(1, 30)}"
        rows.append({"date": when.isoformat(timespec="minutes"), "text": text, "source": source,
                     "platform": platform, "url": f"https://example.com/{platform}/{i + 1}",
                     "author": "" if platform == "news" else source.lstrip("@"),
                     "engagement": "" if platform == "news" else rng.randrange(0, 2000),
                     "sentiment": sentiment})
    rows.sort(key=lambda r: r["date"])
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {path}")


DATASET_TOPICS = {
    **TOPICS,
    "sampah": ["Tumpukan sampah di pinggir sungai belum diangkut.", "Bank sampah warga mulai ramai.",
               "Jadwal angkut sampah berubah minggu ini."],
    "kemacetan": ["Kemacetan panjang di simpang empat pagi ini.", "Jalan layang baru mengurangi kemacetan.",
                  "Pengalihan arus membuat kemacetan di pusat kota."],
    "pasar malam": ["Pasar malam di lapangan kota dibuka lagi.", "Pengunjung pasar malam membludak.",
                    "Pasar malam ditutup lebih awal karena hujan."],
}
DATASET_SENTIMENT = {**SENTIMENT, "belum diangkut": "negative", "mulai ramai": "positive",
                     "Kemacetan panjang": "negative", "mengurangi": "positive", "membludak": "positive",
                     "ditutup lebih awal": "negative"}
# planted spikes: (topic, day index, extra posts) so spike detection has known answers
SPIKES = [("banjir", 12, 120), ("harga cabai", 30, 90), ("pasar malam", 47, 70)]
EMPTY_DAY = 21  # no posts at all on this day


def _post(rng, i, topic, when):
    text = rng.choice(DATASET_TOPICS[topic])
    platform = rng.choice(PLATFORMS)
    source = "Kabar Kota Contoh" if platform == "news" else f"@akun_contoh_{rng.randrange(1, 200)}"
    return {"date": when.isoformat(timespec="seconds"), "text": text, "source": source, "platform": platform,
            "url": f"https://example.com/{platform}/{i}",
            "author": None if platform == "news" else source.lstrip("@"),
            "engagement": None if platform == "news" else int(rng.paretovariate(1.3) * 20),
            "sentiment": next((s for k, s in DATASET_SENTIMENT.items() if k in text), "neutral")}


def make_dataset(path=None, n=2000, days=60, seed=11, start=datetime(2026, 8, 1, tzinfo=WIB)):
    """About n posts over days days, with planted spikes, an empty day and posts right at the WIB day
    boundary (23:30 and 00:10). Returns (path, sha256)."""
    path = Path(path or Path(__file__).parent.parent / "data" / "synthetic" / "posts.jsonl")
    rng = random.Random(seed)
    rows, topics = [], list(DATASET_TOPICS)
    extra = sum(s[2] for s in SPIKES)
    for _ in range(n - extra - 4):
        day = rng.randrange(days)
        while day == EMPTY_DAY:
            day = rng.randrange(days)
        rows.append((rng.choice(topics), start + timedelta(days=day, minutes=rng.randrange(24 * 60))))
    for topic, day, count in SPIKES:
        rows += [(topic, start + timedelta(days=day, minutes=rng.randrange(24 * 60))) for _ in range(count)]
    # edge cases at the WIB day boundary, on days 5 and 6
    rows += [("banjir", start + timedelta(days=5, hours=23, minutes=30)),
             ("banjir", start + timedelta(days=6, minutes=10)),
             ("sampah", start + timedelta(days=5, hours=23, minutes=59, seconds=59)),
             ("sampah", start + timedelta(days=6))]
    rows.sort(key=lambda r: r[1])
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for i, (topic, when) in enumerate(rows, start=1):
            f.write(json.dumps(_post(rng, i, topic, when), ensure_ascii=False) + "\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f"wrote {len(rows)} posts to {path} (sha256 {digest})")
    return path, digest


if __name__ == "__main__":
    if sys.argv[1:] == ["dataset"]:
        make_dataset()
    else:
        main()
