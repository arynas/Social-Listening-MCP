"""Write examples/posts_sample.csv: synthetic posts for demos and tests (fixed seed, so the file is
the same every run). All text, names and links are invented."""
import csv
import random
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


if __name__ == "__main__":
    main()
