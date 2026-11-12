"""Build the benchmark question set from templates, a fixed topic list and a seed.

    python -m slmcp.bench.build --config config.snapshot.example.yaml --out benchmark/questions-v1.jsonl

Every question exists in Indonesian and English (same pair_id). Gold answers are computed by code from
the data through the same recap functions the tools use, so they can be recomputed and checked.

One JSON object per line:
  id, pair_id, lang, type, question,
  expected: list of tool calls that answer it ({"tool", "keyword", and "period" or "start_date"/"end_date"}),
  gold: what a correct answer contains (depends on rule),
  abstain: true when the right answer is "no data" or "out of scope",
  rule: the scoring rule (count, sentiment, peak, compare, links, abstain, no_tool).
"""
import argparse
import json
import random
from datetime import date, timedelta
from pathlib import Path

from slmcp.config import build_adapter, load_config
from slmcp.recap import from_config, news_recap, social_recap

VERSION = "v1"
SEED = 2026

# Topics with enough headlines in the snapshot; keywords chosen so they don't match inside other words.
TOPICS = {
    "banjir": "floods", "gempa": "earthquakes", "kesehatan": "health", "pendidikan": "education",
    "korupsi": "corruption", "kebakaran": "fires", "saham": "stocks", "timnas": "the national team",
    "pajak": "taxes", "cuaca": "the weather", "sampah": "waste", "narkoba": "drugs", "ekspor": "exports",
    "bbm": "fuel", "pariwisata": "tourism", "beasiswa": "scholarships", "mobil listrik": "electric cars",
    "inflasi": "inflation", "sepak bola": "football", "stunting": "stunting", "harga beras": "rice prices",
    "judi online": "online gambling",
}

MONTH_ID = ("Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September",
            "Oktober", "November", "Desember")
MONTH_EN = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
            "October", "November", "December")
FIRST, LAST = date(2026, 10, 1), date(2026, 10, 14)

# relative period words: (Indonesian, English, answerable with the snapshot and frozen today 15 Oct?)
PERIODS = {
    "kemarin": ("kemarin", "yesterday", True),
    "minggu lalu": ("minggu lalu", "last week", True),
    "hari ini": ("hari ini", "today", False),
    "minggu ini": ("minggu ini", "this week", False),
    "bulan ini": ("bulan ini", "this month", False),
    "bulan lalu": ("bulan lalu", "last month", False),
    "7 hari terakhir": ("dalam 7 hari terakhir", "in the last 7 days", False),
    "30 hari terakhir": ("dalam 30 hari terakhir", "in the last 30 days", False),
}


def span_text(a, b, lang):
    """'dari 5 sampai 9 Oktober 2026' / 'from 5 to 9 October 2026' (or one day)."""
    months = MONTH_ID if lang == "id" else MONTH_EN
    if a == b:
        return (f"pada {a.day} {months[a.month - 1]} {a.year}" if lang == "id"
                else f"on {a.day} {months[a.month - 1]} {a.year}")
    left = f"{a.day}" if (a.year, a.month) == (b.year, b.month) else (
        f"{a.day} {months[a.month - 1]}" + ("" if a.year == b.year else f" {a.year}"))
    right = f"{b.day} {months[b.month - 1]} {b.year}"
    return f"dari {left} sampai {right}" if lang == "id" else f"from {left} to {right}"


def kw(keyword, lang):
    return keyword if lang == "id" else f'"{keyword}" ({TOPICS.get(keyword, "")})'.replace(" ()", "")


class Builder:
    def __init__(self, config):
        self.adapter = build_adapter(config)
        self.options = from_config(config)
        self.rng = random.Random(SEED)
        self.items = []
        self.n = 0

    # --- tool access (same functions as the MCP tools) ---
    def recap(self, call):
        fn = social_recap if call["tool"] == "social_recap" else news_recap
        args = {k: v for k, v in call.items() if k != "tool"}
        return fn(self.adapter, args.pop("keyword"), args.get("start_date"), args.get("end_date"),
                  period=args.get("period"), **self.options)

    def add(self, typ, questions, expected, gold, abstain, rule):
        self.n += 1
        pair = f"q{self.n:03d}"
        for lang in ("id", "en"):
            self.items.append({"id": f"{pair}-{lang}", "pair_id": pair, "lang": lang, "type": typ,
                               "question": questions[lang], "expected": expected, "gold": gold,
                               "abstain": abstain, "rule": rule})

    def random_span(self, min_days=1, max_days=7):
        days = self.rng.randint(min_days, max_days)
        start = FIRST + timedelta(days=self.rng.randint(0, (LAST - FIRST).days - days + 1))
        return start, start + timedelta(days=days - 1)

    @staticmethod
    def dates(a, b):
        return {"start_date": a.isoformat(), "end_date": b.isoformat()}

    def topic(self, k=1):
        return self.rng.sample(sorted(TOPICS), k) if k > 1 else self.rng.choice(sorted(TOPICS))

    # --- question types ---
    def count_fixed(self, n):
        for _ in range(n):
            k, (a, b) = self.topic(), self.random_span(1, 7)
            call = {"tool": "news_recap", "keyword": k, **self.dates(a, b)}
            r = self.recap(call)
            self.add("count_fixed", {
                "id": f"Berapa jumlah berita tentang {k} {span_text(a, b, 'id')}?",
                "en": f"How many news headlines about {kw(k, 'en')} were there {span_text(a, b, 'en')}?"},
                [call], {"total": r["total"], "start": r["start_date"], "end": r["end_date"]}, False, "count")

    def count_relative(self, word, n):
        id_word, en_word, _ = PERIODS[word]
        for _ in range(n):
            k = self.topic()
            call = {"tool": "news_recap", "keyword": k, "period": word}
            r = self.recap(call)
            self.add("count_relative", {
                "id": f"Berapa jumlah berita tentang {k} {id_word}?",
                "en": f"How many news headlines about {kw(k, 'en')} were there {en_word}?"},
                [call], {"total": r["total"], "start": r["start_date"], "end": r["end_date"]}, False, "count")

    def sentiment(self, n):
        for i in range(n):
            k = self.topic()
            if i % 3 == 2:
                call = {"tool": "news_recap", "keyword": k, "period": "minggu lalu"}
                pid, pen = "minggu lalu", "last week"
            else:
                a, b = self.random_span(3, 14)
                call = {"tool": "news_recap", "keyword": k, **self.dates(a, b)}
                pid, pen = span_text(a, b, "id"), span_text(a, b, "en")
            r = self.recap(call)
            st = r["sentiment_total"]
            self.add("sentiment", {
                "id": f"Bagaimana sentimen berita tentang {k} {pid}?",
                "en": f"What is the sentiment of news about {kw(k, 'en')} {pen}?"},
                [call], {"total": r["total"], "counts": st["counts"], "pct": st["pct"],
                         "start": r["start_date"], "end": r["end_date"]}, False, "sentiment")

    def peak(self, n):
        for i in range(n):
            k = self.topic()
            a, b = (FIRST, LAST) if i % 2 == 0 else self.random_span(5, 10)
            call = {"tool": "news_recap", "keyword": k, **self.dates(a, b)}
            r = self.recap(call)
            pts = r["trend"]["points"]
            top = max(p["total"] for p in pts)
            self.add("peak", {
                "id": f"Hari apa berita tentang {k} paling banyak, {span_text(a, b, 'id')}? "
                      "Berapa jumlahnya?",
                "en": f"On which day were there the most news headlines about {kw(k, 'en')}, "
                      f"{span_text(a, b, 'en')}? How many?"},
                [call], {"peak_days": [p["start"] for p in pts if p["total"] == top], "peak_count": top,
                         "start": r["start_date"], "end": r["end_date"]}, False, "peak")

    def compare(self, n):
        for i in range(n):
            k1, k2 = self.topic(2)
            if i % 2 == 0:
                ex = {"period": "minggu lalu"}
                pid, pen = "minggu lalu", "last week"
            else:
                a, b = self.random_span(3, 7)
                ex = self.dates(a, b)
                pid, pen = span_text(a, b, "id"), span_text(a, b, "en")
            calls = [{"tool": "news_recap", "keyword": k, **ex} for k in (k1, k2)]
            r1, r2 = self.recap(calls[0]), self.recap(calls[1])
            larger = k1 if r1["total"] > r2["total"] else k2 if r2["total"] > r1["total"] else "equal"
            self.add("compare", {
                "id": f"Bandingkan jumlah berita tentang {k1} dan {k2} {pid}. Mana yang lebih banyak?",
                "en": f"Compare the number of news headlines about {kw(k1, 'en')} and {kw(k2, 'en')} {pen}. "
                      "Which has more?"},
                calls, {"totals": {k1: r1["total"], k2: r2["total"]}, "larger": larger,
                        "start": r1["start_date"], "end": r1["end_date"]}, False, "compare")

    def links(self, n):
        for i in range(n):
            k = self.topic()
            if i % 2 == 0:
                call = {"tool": "news_recap", "keyword": k, "period": "kemarin"}
                pid, pen = "kemarin", "yesterday"
            else:
                a, b = self.random_span(1, 3)
                call = {"tool": "news_recap", "keyword": k, **self.dates(a, b)}
                pid, pen = span_text(a, b, "id"), span_text(a, b, "en")
            r = self.recap(call)
            self.add("links", {
                "id": f"Berikan 3 contoh judul berita tentang {k} {pid}, lengkap dengan link-nya.",
                "en": f"Give 3 example news headlines about {kw(k, 'en')} {pen}, with their links."},
                [call], {"total": r["total"], "sample_urls": [s["url"] for s in r["samples"]],
                         "start": r["start_date"], "end": r["end_date"]}, False, "links")

    def abstain_relative(self, words, per_word):
        for word in words:
            id_word, en_word, _ = PERIODS[word]
            for j in range(per_word):
                k = self.topic()
                call = {"tool": "news_recap", "keyword": k, "period": word}
                r = self.recap(call)
                assert r["data_available"] is False, (word, r)
                count_q = {"id": f"Berapa jumlah berita tentang {k} {id_word}?",
                           "en": f"How many news headlines about {kw(k, 'en')} were there {en_word}?"}
                sentiment_q = {"id": f"Bagaimana sentimen berita tentang {k} {id_word}?",
                               "en": f"What is the sentiment of news about {kw(k, 'en')} {en_word}?"}
                q = count_q if j % 2 == 0 else sentiment_q
                self.add("abstain_relative", q, [call], self.no_data_gold(r), True, "abstain")

    def abstain_dates(self, typ, spans):
        for a, b in spans:
            k = self.topic()
            call = {"tool": "news_recap", "keyword": k, **self.dates(a, b)}
            r = self.recap(call)
            assert r["data_available"] is False, (a, b, r)
            if (a.day == 1 and (b + timedelta(days=1)).day == 1 and a.month == b.month):
                pid = f"pada bulan {MONTH_ID[a.month - 1]} {a.year}"
                pen = f"in {MONTH_EN[a.month - 1]} {a.year}"
            else:
                pid, pen = span_text(a, b, "id"), span_text(a, b, "en")
            self.add(typ, {"id": f"Bagaimana sentimen berita tentang {k} {pid}?",
                           "en": f"What is the sentiment of news about {kw(k, 'en')} {pen}?"},
                     [call], self.no_data_gold(r), True, "abstain")

    @staticmethod
    def no_data_gold(r):
        return {"data_available": False, "must_tell_user": r["must_tell_user"],
                "asked": [r["asked_period"]["start"], r["asked_period"]["end"]]}

    def out_of_scope(self):
        plain = [
            ("Siapa presiden Indonesia saat ini?", "Who is the current president of Indonesia?"),
            ("Bagaimana prakiraan cuaca di Jakarta besok?",
             "What is the weather forecast for Jakarta tomorrow?"),
            ("Tuliskan puisi pendek tentang laut.", "Write a short poem about the sea."),
            ("Berapa harga saham BBCA besok?", "What will the BBCA share price be tomorrow?"),
        ]
        for qid, qen in plain:
            self.add("out_of_scope", {"id": qid, "en": qen}, [], {"expect_tool": False}, True, "no_tool")
        for k in self.topic(2):
            call = {"tool": "social_recap", "keyword": k, "period": "minggu lalu"}
            r = self.recap(call)
            assert r["data_available"] is False
            self.add("out_of_scope", {
                "id": f"Apa kata orang di media sosial tentang {k} minggu lalu?",
                "en": f"What did people say on social media about {kw(k, 'en')} last week?"},
                [call], self.no_data_gold(r), True, "abstain")

    def odd_keywords(self):
        a, b = date(2026, 10, 5), date(2026, 10, 11)
        cases = [  # (keyword asked in the question, keyword the tool should get)
            ("zxqvbn", "zxqvbn"), ("banjjir", "banjjir"), ("BANJIR", "banjir"), ("Korupsi", "korupsi"),
            ("kebakaran hutan dan lahan di Kalimantan", "kebakaran"), ("harga beras di pasar", "harga beras"),
        ]
        for asked, key in cases:
            call = {"tool": "news_recap", "keyword": key, **self.dates(a, b)}
            r = self.recap(call)
            self.add("odd_keyword", {
                "id": f"Berapa jumlah berita tentang {asked} {span_text(a, b, 'id')}?",
                "en": f'How many news headlines about "{asked}" were there {span_text(a, b, "en")}?'},
                [call], {"total": r["total"], "start": r["start_date"], "end": r["end_date"],
                         "note": "a longer keyword may also be right; read manually if the total differs"},
                False, "count")

    def build(self):
        self.count_fixed(16)
        self.count_relative("kemarin", 8)
        self.count_relative("minggu lalu", 8)
        self.sentiment(12)
        self.peak(10)
        self.compare(8)
        self.links(8)
        self.abstain_relative(["hari ini", "minggu ini", "bulan ini", "bulan lalu", "7 hari terakhir",
                               "30 hari terakhir"], 2)
        self.abstain_dates("abstain_future", [
            (date(2026, 12, 1), date(2026, 12, 31)), (date(2026, 11, 1), date(2026, 11, 30)),
            (date(2026, 10, 20), date(2026, 10, 26)), (date(2027, 1, 1), date(2027, 1, 31)),
            (date(2026, 11, 9), date(2026, 11, 15)), (date(2026, 12, 24), date(2026, 12, 26)),
            (date(2026, 10, 16), date(2026, 10, 16)), (date(2027, 3, 1), date(2027, 3, 31))])
        self.abstain_dates("abstain_past", [
            (date(2026, 9, 1), date(2026, 9, 30)), (date(2026, 8, 1), date(2026, 8, 31)),
            (date(2026, 9, 21), date(2026, 9, 27)), (date(2026, 1, 1), date(2026, 1, 31)),
            (date(2025, 10, 1), date(2025, 10, 14)), (date(2026, 9, 30), date(2026, 9, 30))])
        self.abstain_dates("long_range", [
            (date(2026, 1, 1), date(2026, 10, 14)), (date(2025, 10, 15), date(2026, 10, 14)),
            (date(2026, 7, 1), date(2026, 10, 14)), (date(2026, 9, 15), date(2026, 10, 14)),
            (date(2026, 10, 1), date(2026, 10, 31))])
        self.out_of_scope()
        self.odd_keywords()
        return self.items


def build(config_path):
    return Builder(load_config(config_path)).build()


def dumps(items):
    return "".join(json.dumps(i, ensure_ascii=False, sort_keys=True) + "\n" for i in items)


def main(argv=None):
    ap = argparse.ArgumentParser(description="build the benchmark question set")
    ap.add_argument("--config", default="config.snapshot.example.yaml")
    ap.add_argument("--out", default=f"benchmark/questions-{VERSION}.jsonl")
    args = ap.parse_args(argv)
    items = build(args.config)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(dumps(items), encoding="utf-8")
    print(f"{len(items)} items ({len(items) // 2} pairs) -> {args.out}")


if __name__ == "__main__":
    main()
