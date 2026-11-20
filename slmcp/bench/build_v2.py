"""Build benchmark v2: the v1.1 questions re-scored on the 44-day snapshot, plus new questions for
detect_spikes, compare_keywords and entity_trend, and "trap" questions whose right tool is an old one.

    python -m slmcp.bench.build_v2 --config config.snapshot-s1.example.yaml --out benchmark/questions-v2.jsonl

Gold answers are computed by code through the same functions the MCP tools use (never typed by hand),
on the release data-2026-10-v2 (1 Sep - 14 Oct 2026). benchmark/questions-v1.1.jsonl is not changed.

Each item has the v1.1 fields plus:
  tools: {"best": [...], "ok": [...]}: tools that answer it (best) or also give the right numbers (ok),
  kind: normal, abstain or trap (a question with words like "naik"/"bandingkan" whose right tool is old),
  origin: v1.1 or v2; v2_note: why a v1.1 gold changed on the 44-day data.
"""

import argparse
import hashlib
import json
import random
from datetime import date, timedelta
from pathlib import Path

from slmcp.bench.build import MONTH_EN, MONTH_ID, TOPICS, WEEKDAYS_EN, span_text, stories
from slmcp.compare import compare_keywords
from slmcp.compare import from_config as compare_config
from slmcp.config import build_adapter, load_config
from slmcp.recap import data_range, from_config, human_range, news_recap, relative_dates, social_recap
from slmcp.spikes import detect_spikes
from slmcp.spikes import from_config as spikes_config
from slmcp.trend import entity_trend, group_stories
from slmcp.trend import from_config as trend_config

VERSION = "v2"
SEED = 2026
V11 = "benchmark/questions-v1.1.jsonl"
DATA_RELEASE = "data-2026-10-v2"
DATA_SHA256 = {  # unzipped files of the release; the build stops if the local files differ
    "posts-2026-09.labelled.jsonl": "aa901105550abadbd99668d521f5782a7739a56b45bdadabdd33156f9b00735f",
    "posts-2026-10.labelled.jsonl": "eded825aad992856e2c734169b11db9aeb72342eb21d2901704287e2e6b09624",
}
SEP1, SEP30, OCT1, OCT14 = date(2026, 9, 1), date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 14)

# Topics for the new questions (glosses for the English questions); all have headlines in the 44 days.
NEW_TOPICS = {
    **TOPICS,
    "krakatau": "the Krakatau volcano",
    "ott": "KPK sting operations (OTT)",
    "kpk": "the KPK",
    "reshuffle": "the cabinet reshuffle",
    "persija": "Persija",
    "prabowo": "Prabowo",
    "demo": "protests",
    "solok": "Solok",
    "harga cabai": "chilli prices",
    "dbd": "dengue fever (DBD)",
    "kapal virgo": "the Virgo ship accident",
    "gontor": "Pondok Gontor",
    "bangladesh": "Bangladesh",
    "pancasila": "Pancasila",
    "juara fifa": "the FIFA championship",
}
NONSENSE = "zxqvbn"
SWAP_NO_SPIKE = 3
# (keyword, day) of events in the S1-0 events list that Ari marked "benar" (hand check, 20 Nov 2026);
# events named after a private person in a criminal case are left out (questions are about topics, not people)
EVENTS_BENAR = [
    ("gempa", "2026-09-01"),
    ("persija", "2026-09-12"),
    ("kapal virgo", "2026-09-13"),
    ("ott", "2026-09-15"),
    ("gempa", "2026-09-17"),
    ("gontor", "2026-09-19"),
    ("solok", "2026-09-22"),
    ("bangladesh", "2026-10-01"),
    ("pancasila", "2026-10-01"),
    ("juara fifa", "2026-10-06"),
    ("ott", "2026-10-14"),
]


def kw2(keyword, lang):
    gloss = NEW_TOPICS.get(keyword, "")
    return keyword if lang == "id" or not gloss else f'"{keyword}" ({gloss})'


def month_text(a, lang):
    return (
        f"pada bulan {MONTH_ID[a.month - 1]} {a.year}"
        if lang == "id"
        else f"in {MONTH_EN[a.month - 1]} {a.year}"
    )


def period_text(a, b, lang):
    whole = a.day == 1 and (b + timedelta(days=1)).day == 1 and a.month == b.month
    return month_text(a, lang) if whole else span_text(a, b, lang)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def no_data_gold(r):
    return {
        "data_available": False,
        "must_tell_user": r["must_tell_user"],
        "asked": [r["asked_period"]["start"], r["asked_period"]["end"]],
    }


# --- gold from tool results: used by the builder and by slmcp.bench.check (so gold is reproducible) ---


def gold_spike(r):
    events = [
        {"start": s["start"], "peak": s["peak"], "end": s["end"], "count": s["count"]}
        for s in r["spikes"][:3]
    ]
    g = {
        "spike_count": r["spike_count"],
        "unit": r["unit"],
        "total": r["total"],
        "events": events,
        "start": r["start_date"],
        "end": r["end_date"],
    }
    if events:
        g.update(top_peak=events[0]["peak"], top_count=events[0]["count"])
    return g


def gold_compare(r):
    per = {e["keyword"]: e for e in r["per_keyword"]}
    totals = {k: e["total"] for k, e in per.items()}
    best = max(totals.values())
    leaders = [k for k, v in totals.items() if v == best]
    p = r["pairs"][0]
    unit = r["trend"]["unit"]
    return {
        "totals": totals,
        "larger": leaders[0] if len(leaders) == 1 else "equal",
        "negative_share": {k: e["negative_share"] for k, e in per.items()},
        "total_ratio": p["total_ratio"],
        "total_diff": p["total_diff"],
        "days_a_higher": p.get(f"{unit}s_a_higher"),
        "days_b_higher": p.get(f"{unit}s_b_higher"),
        "overlap": r["overlap"]["headlines_in_2_or_more"],
        "start": r["start_date"],
        "end": r["end_date"],
    }


def gold_trend(r):
    ch = r["change"]
    t = r["trend"]
    top = max(t["total"]) if t.get("total") else 0
    g = {
        "total": r["total"],
        "peak_count": top,
        "start": r["start_date"],
        "end": r["end_date"],
        "peak_days": [x for x, n in zip(t.get("labels", []), t.get("total", []), strict=True) if n == top],
        "previous_total": ch["previous_total"] if ch else None,
        "pct_change": ch["pct_change"] if ch else None,
        "negative_share_diff": ch["negative_share_diff"] if ch else None,
        "change_given": ch is not None,
        "top_stories": [
            {"text": s["text"], "url": s["url"], "sources": s["sources"]} for s in r["top_stories"][:3]
        ],
    }
    return g


GOLD_FROM = {"spike": gold_spike, "compare": gold_compare, "trend": gold_trend}


class BuilderV2:
    def __init__(self, config, v11_path=V11, check_sha=True):
        self.config = config
        if check_sha:
            for p in config.data_source.paths:
                want = DATA_SHA256.get(Path(p).name)
                got = sha256(p)
                if want and got != want:
                    raise SystemExit(f"{p}: sha256 {got} is not the {DATA_RELEASE} file ({want})")
        self.adapter = build_adapter(config)
        self.opts = {
            "recap": from_config(config),
            "spikes": spikes_config(config),
            "compare": compare_config(config),
            "trend": trend_config(config),
        }
        self.today = date.fromisoformat(config.today)
        self.rel = relative_dates(self.today)
        first, last = data_range(self.adapter.posts)
        self.context = (
            f"today = {self.today.day} {MONTH_EN[self.today.month - 1][:3]} {self.today.year} "
            f"({WEEKDAYS_EN[self.today.weekday()]}); data = {human_range(first, last)}"
        )
        self.v11_path = v11_path
        self.rng = random.Random(SEED)
        self.items, self.n, self.changes = [], 0, []

    # --- tool access (same functions as the MCP tools) ---
    def call(self, c):
        args = {k: v for k, v in c.items() if k != "tool"}
        when = (args.get("start_date"), args.get("end_date"))
        period = args.get("period")
        if c["tool"] in ("news_recap", "social_recap"):
            fn = social_recap if c["tool"] == "social_recap" else news_recap
            return fn(self.adapter, args["keyword"], *when, period=period, **self.opts["recap"])
        if c["tool"] == "detect_spikes":
            return detect_spikes(self.adapter, args["keyword"], *when, period=period, **self.opts["spikes"])
        if c["tool"] == "compare_keywords":
            return compare_keywords(
                self.adapter, args["keywords"], *when, period=period, **self.opts["compare"]
            )
        if c["tool"] == "entity_trend":
            return entity_trend(self.adapter, args["keyword"], *when, period=period, **self.opts["trend"])
        raise ValueError(c["tool"])

    def with_context(self, gold, expected):
        words = [c["period"] for c in expected if c.get("period")]
        if not words:
            return gold
        a, b = (date.fromisoformat(x) for x in self.rel[words[0]])
        return {**gold, "context": f"{self.context}; {words[0]} = {human_range(a, b)}"}

    # --- v1.1 items, gold recomputed on the 44-day data ---
    def regold(self, item):
        rule, expected = item["rule"], item["expected"]
        res = [self.call(c) for c in expected]
        old = item["gold"]
        new = dict(item)
        best = sorted({c["tool"] for c in expected})
        ok = []
        if rule == "compare":
            ok = ["compare_keywords"]
        if rule == "peak":
            ok = ["entity_trend"]
        note = None
        if rule == "no_tool":
            gold = old
        elif rule == "abstain":
            if all(r.get("data_available") is False for r in res):
                gold = no_data_gold(res[0])
            else:  # September is inside the 44-day data, so this is answerable now
                r = res[0]
                if item["question"].startswith(("Berapa", "How many")):
                    rule, gold = (
                        "count",
                        {"total": r["total"], "start": r["start_date"], "end": r["end_date"]},
                    )
                else:
                    st = r["sentiment_total"]
                    rule, gold = (
                        "sentiment",
                        {
                            "total": r["total"],
                            "counts": st["counts"],
                            "pct": st["pct"],
                            "start": r["start_date"],
                            "end": r["end_date"],
                        },
                    )
                new["abstain"] = False
                note = "answerable with the 44-day data (no data in v1.1)"
        else:
            r = res[0]
            if rule == "count":
                gold = {**old, "total": r["total"], "start": r["start_date"], "end": r["end_date"]}
                if old.get("alt"):
                    fixed = self.call({**expected[0], "keyword": old["alt"]["keyword"]})
                    gold["alt"] = {**old["alt"], "total": fixed["total"]}
            elif rule == "sentiment":
                st = r["sentiment_total"]
                gold = {**old, "total": r["total"], "counts": st["counts"], "pct": st["pct"]}
            elif rule == "peak":
                pts = r["trend"]["points"]
                top = max(p["total"] for p in pts)
                gold = {**old, "peak_days": [p["start"] for p in pts if p["total"] == top], "peak_count": top}
            elif rule == "compare":
                totals = {c["keyword"]: x["total"] for c, x in zip(expected, res, strict=True)}
                k1, k2 = list(totals)
                larger = k1 if totals[k1] > totals[k2] else k2 if totals[k2] > totals[k1] else "equal"
                gold = {**old, "totals": totals, "larger": larger}
            elif rule == "links":
                story = stories(r["samples"])
                gold = {
                    **old,
                    "total": r["total"],
                    "sample_urls": [s["url"] for s in r["samples"]],
                    "stories": story,
                }
            else:
                raise ValueError(rule)
        gold = self.with_context({k: v for k, v in gold.items() if k != "context"}, expected)
        answer_keys = set(gold) - {"context", "must_tell_user"}
        changed = {
            k for k in answer_keys | (set(old) - {"context", "must_tell_user"}) if gold.get(k) != old.get(k)
        }
        if changed or rule != item["rule"]:
            note = note or f"gold changed on the 44-day data: {', '.join(sorted(changed))}"
            self.changes.append((item["id"], note))
        elif gold.get("must_tell_user") != old.get("must_tell_user"):
            note = "same answer; the no-data sentence now names the 44-day data range"
        new.update(
            gold=gold,
            rule=rule,
            origin="v1.1",
            tools={"best": best, "ok": ok},
            kind="abstain" if new["abstain"] else "normal",
            tool=best[0] if best else "none",
        )
        if note:
            new["v2_note"] = note
        return new

    # --- new items ---
    def add(self, typ, kind, questions, expected, gold, rule, best, ok=()):
        self.n += 1
        pair = f"n{self.n:03d}"
        gold = self.with_context(gold, expected)
        abstain = kind == "abstain"
        for lang in ("id", "en"):
            self.items.append(
                {
                    "id": f"{pair}-{lang}",
                    "pair_id": pair,
                    "lang": lang,
                    "type": typ,
                    "kind": kind,
                    "question": questions[lang],
                    "expected": expected,
                    "gold": gold,
                    "abstain": abstain,
                    "rule": rule,
                    "origin": "v2",
                    "tool": best[0],
                    "tools": {"best": list(best), "ok": list(ok)},
                }
            )

    def span(self, lo, hi, min_days, max_days):
        days = self.rng.randint(min_days, max_days)
        start = lo + timedelta(days=self.rng.randint(0, (hi - lo).days - days + 1))
        return start, start + timedelta(days=days - 1)

    @staticmethod
    def dates(a, b):
        return {"start_date": a.isoformat(), "end_date": b.isoformat()}

    def pick(self, pool):
        return self.rng.choice(sorted(pool))

    def abstain(self, typ, tool, args, questions, best, ok=()):
        r = self.call({"tool": tool, **args})
        assert r.get("data_available") is False, (typ, args, r)
        self.add(typ, "abstain", questions, [{"tool": tool, **args}], no_data_gold(r), "abstain", best, ok)

    def spikes(self):
        q = {
            "id": "Kapan berita tentang {k} melonjak {p}? Sebutkan tanggal puncak dan jumlah beritanya.",
            "en": "When did news about {k} spike {p}? Give the peak date and the number of headlines.",
        }
        pool = [
            "krakatau",
            "banjir",
            "gempa",
            "ott",
            "reshuffle",
            "persija",
            "kpk",
            "solok",
            "timnas",
            "prabowo",
            "judi online",
            "kebakaran",
            "harga beras",
            "demo",
            "pendidikan",
            "kesehatan",
        ]
        spans = [
            (SEP1, SEP30),
            (SEP1, SEP30),
            (SEP1, SEP30),
            (OCT1, OCT14),
            (OCT1, OCT14),
            self.span(SEP1, OCT14, 10, 21),
            self.span(SEP1, OCT14, 10, 21),
            self.span(SEP1, OCT14, 10, 21),
        ]
        used = self.rng.sample(pool, len(spans))
        plans = []
        for k, (a, b) in zip(used, spans, strict=True):
            c = {"tool": "detect_spikes", "keyword": k, **self.dates(a, b)}
            plans.append((k, a, b, c, gold_spike(self.call(c))))
        # Ari's choice (20 Nov 2026): the first SWAP_NO_SPIKE fixed-range items without a spike are replaced
        # by ranges around hand-checked events where detect_spikes does find one (own seeded order, so the
        # rest of the set stays the same).
        no_spike = [i for i, p in enumerate(plans) if not p[4].get("top_peak")][:SWAP_NO_SPIKE]
        for i, new in zip(no_spike, self.spike_swaps({p[0] for p in plans}), strict=True):
            plans[i] = new
        for k, a, b, c, g in plans:
            self.add(
                "spike_fixed",
                "normal",
                {lang: q[lang].format(k=kw2(k, lang), p=period_text(a, b, lang)) for lang in q},
                [c],
                g,
                "spike",
                ["detect_spikes"],
            )
        rel = {
            "id": "Apakah ada lonjakan berita tentang {k} {w}? Kapan puncaknya?",
            "en": "Was there a spike in news about {k} {w}? When was the peak?",
        }
        for k, word, en in [
            (self.pick(pool), "bulan lalu", "last month"),
            (self.pick(pool), "bulan lalu", "last month"),
            (self.pick(pool), "minggu lalu", "last week"),
            (self.pick(pool), "minggu lalu", "last week"),
        ]:
            c = {"tool": "detect_spikes", "keyword": k, "period": word}
            self.add(
                "spike_relative",
                "normal",
                {"id": rel["id"].format(k=k, w=word), "en": rel["en"].format(k=kw2(k, "en"), w=en)},
                [c],
                gold_spike(self.call(c)),
                "spike",
                ["detect_spikes"],
            )
        for k in ("kpk", "prabowo"):
            c = {"tool": "detect_spikes", "keyword": k, "period": "kemarin"}
            self.add(
                "spike_hourly",
                "normal",
                {
                    "id": f"Pada jam berapa berita tentang {k} melonjak kemarin?",
                    "en": f"At what hour did news about {kw2(k, 'en')} spike yesterday?",
                },
                [c],
                gold_spike(self.call(c)),
                "spike",
                ["detect_spikes"],
            )
        for k, args, pid, pen in [
            (
                "banjir",
                self.dates(date(2026, 12, 1), date(2026, 12, 31)),
                "pada bulan Desember 2026",
                "in December 2026",
            ),
            (
                "gempa",
                self.dates(date(2026, 8, 1), date(2026, 8, 31)),
                "pada bulan Agustus 2026",
                "in August 2026",
            ),
            ("timnas", {"period": "bulan ini"}, "bulan ini", "this month"),
        ]:
            self.abstain(
                "spike_abstain",
                "detect_spikes",
                {"keyword": k, **args},
                {
                    "id": f"Kapan berita tentang {k} melonjak {pid}?",
                    "en": f"When did news about {kw2(k, 'en')} spike {pen}?",
                },
                ["detect_spikes"],
            )
        c = {"tool": "detect_spikes", "keyword": NONSENSE, "period": "bulan lalu"}
        self.add(
            "spike_nonsense",
            "normal",
            {
                "id": f"Kapan berita tentang {NONSENSE} melonjak bulan lalu?",
                "en": f'When did news about "{NONSENSE}" spike last month?',
            },
            [c],
            gold_spike(self.call(c)),
            "spike",
            ["detect_spikes"],
        )

    def spike_swaps(self, taken):
        """SWAP_NO_SPIKE ranges (7 days before to 6 days after a hand-checked event, inside the data) where
        detect_spikes finds a spike within a day of the event, each with a keyword not used yet."""
        rng = random.Random(SEED)
        events = list(EVENTS_BENAR)
        rng.shuffle(events)
        out = []
        for k, day in events:
            if k in taken or len(out) == SWAP_NO_SPIKE:
                continue
            d = date.fromisoformat(day)
            a, b = max(SEP1, d - timedelta(days=7)), min(OCT14, d + timedelta(days=6))
            c = {"tool": "detect_spikes", "keyword": k, **self.dates(a, b)}
            g = gold_spike(self.call(c))
            if g.get("top_peak") and abs((date.fromisoformat(g["top_peak"][:10]) - d).days) <= 1:
                out.append((k, a, b, c, g))
                taken = taken | {k}
        return out

    def compares(self):
        pool = [
            "banjir",
            "gempa",
            "korupsi",
            "kpk",
            "timnas",
            "persija",
            "harga beras",
            "harga cabai",
            "pajak",
            "kesehatan",
            "pendidikan",
            "sampah",
            "bbm",
            "kebakaran",
            "narkoba",
            "pariwisata",
            "cuaca",
            "saham",
        ]
        best, ok = ["compare_keywords"], ["news_recap"]

        def run(ks, a=None, b=None, period=None):
            args = {"period": period} if period else self.dates(a, b)
            c = {"tool": "compare_keywords", "keywords": ks, **args}
            return c, gold_compare(self.call(c))

        def when(a, b, period, lang):
            if period:
                return {
                    "minggu lalu": ("minggu lalu", "last week"),
                    "bulan lalu": ("bulan lalu", "last month"),
                    "kemarin": ("kemarin", "yesterday"),
                }[period][lang == "en"]
            return period_text(a, b, lang)

        plans = [
            ("compare_total", None, None, "bulan lalu"),
            ("compare_total", OCT1, OCT14, None),
            ("compare_total", None, None, "minggu lalu"),
            ("compare_total", *self.span(SEP1, OCT14, 3, 10), None),
            ("compare_total", *self.span(SEP1, OCT14, 3, 10), None),
            ("compare_ratio", SEP1, SEP30, None),
            ("compare_ratio", None, None, "minggu lalu"),
            ("compare_ratio", *self.span(SEP1, OCT14, 5, 14), None),
            ("compare_negative", OCT1, OCT14, None),
            ("compare_negative", None, None, "bulan lalu"),
            ("compare_negative", *self.span(SEP1, OCT14, 5, 14), None),
            ("compare_days", SEP1, SEP30, None),
            ("compare_days", *self.span(SEP1, OCT14, 7, 14), None),
        ]
        for typ, a, b, period in plans:
            k1, k2 = self.rng.sample(pool, 2)
            c, g = run([k1, k2], a, b, period)
            w = {lang: when(a, b, period, lang) for lang in ("id", "en")}
            e1, e2 = kw2(k1, "en"), kw2(k2, "en")
            q = {
                "compare_total": (
                    f"Mana yang lebih banyak diberitakan {w['id']}: {k1} atau {k2}? Berapa jumlah "
                    "masing-masing?",
                    f"Which had more news {w['en']}: {e1} or {e2}? How many headlines each?",
                ),
                "compare_ratio": (
                    f"Berapa kali lipat jumlah berita tentang {k1} dibanding {k2} {w['id']}?",
                    f"How many times as many news headlines were there about {e1} as about {e2} {w['en']}?",
                ),
                "compare_negative": (
                    f"Berita tentang {k1} atau {k2} yang lebih negatif {w['id']}? Berapa persen "
                    "berita negatif masing-masing?",
                    f"Was news about {e1} or {e2} more negative {w['en']}? What share of each was negative?",
                ),
                "compare_days": (
                    f"Pada berapa hari berita tentang {k1} lebih banyak daripada berita tentang {k2} "
                    f"{w['id']}?",
                    f"On how many days were there more headlines about {e1} than about {e2} {w['en']}?",
                ),
            }[typ]
            self.add(
                typ,
                "normal",
                {"id": q[0], "en": q[1]},
                [c],
                g,
                typ if typ != "compare_total" else "compare",
                best,
                ok,
            )
        for _ in range(2):
            k1, k2, k3 = self.rng.sample(pool, 3)
            a, b = self.span(SEP1, OCT14, 3, 14)
            c, g = run([k1, k2, k3], a, b)
            self.add(
                "compare_three",
                "normal",
                {
                    "id": f"Bandingkan jumlah berita tentang {k1}, {k2}, dan {k3} {period_text(a, b, 'id')}.",
                    "en": f"Compare the number of news headlines about {kw2(k1, 'en')}, {kw2(k2, 'en')} and "
                    f"{kw2(k3, 'en')} {period_text(a, b, 'en')}.",
                },
                [c],
                g,
                "compare",
                best,
                ok,
            )
        c, g = run([NONSENSE, "banjir"], period="minggu lalu")
        self.add(
            "compare_nonsense",
            "normal",
            {
                "id": f"Bandingkan jumlah berita tentang {NONSENSE} dan banjir minggu lalu.",
                "en": f'Compare the number of news headlines about "{NONSENSE}" and '
                f"{kw2('banjir', 'en')} last "
                "week.",
            },
            [c],
            g,
            "compare",
            best,
            ok,
        )
        for ks, args, pid, pen in [
            (
                ["banjir", "gempa"],
                self.dates(date(2026, 11, 1), date(2026, 11, 30)),
                "pada bulan November 2026",
                "in November 2026",
            ),
            (
                ["pajak", "bbm"],
                self.dates(date(2026, 8, 25), date(2026, 9, 5)),
                "dari 25 Agustus sampai 5 September 2026",
                "from 25 August to 5 September 2026",
            ),
        ]:
            self.abstain(
                "compare_abstain",
                "compare_keywords",
                {"keywords": ks, **args},
                {
                    "id": f"Bandingkan jumlah berita tentang {ks[0]} dan {ks[1]} {pid}.",
                    "en": f"Compare the number of news headlines about {kw2(ks[0], 'en')} and "
                    f"{kw2(ks[1], 'en')} {pen}.",
                },
                best,
                ok,
            )

    def trends(self):
        pool = [
            "prabowo",
            "kpk",
            "krakatau",
            "banjir",
            "gempa",
            "korupsi",
            "timnas",
            "persija",
            "pajak",
            "kesehatan",
            "pendidikan",
            "harga beras",
            "kebakaran",
            "narkoba",
            "sampah",
            "bbm",
        ]
        best = ["entity_trend"]
        change_q = (
            "Bagaimana tren berita tentang {k} {p} dibanding periode sebelumnya yang sama panjang? Naik "
            "atau turun berapa persen?",
            "How did news about {k} {p} change compared with the previous period of the same length? Up or "
            "down by how many percent?",
        )
        spans = [
            (OCT1, OCT14),
            (date(2026, 9, 16), SEP30),
            self.span(date(2026, 9, 15), OCT14, 5, 14),
            self.span(date(2026, 9, 15), OCT14, 5, 14),
        ]
        for k, (a, b) in zip(self.rng.sample(pool, 4), spans, strict=True):
            c = {"tool": "entity_trend", "keyword": k, **self.dates(a, b)}
            self.add(
                "trend_change",
                "normal",
                {
                    "id": change_q[0].format(k=k, p=period_text(a, b, "id")),
                    "en": change_q[1].format(k=kw2(k, "en"), p=period_text(a, b, "en")),
                },
                [c],
                gold_trend(self.call(c)),
                "trend_change",
                best,
                ["news_recap"],
            )
        for k in self.rng.sample(pool, 2):
            c = {"tool": "entity_trend", "keyword": k, "period": "minggu lalu"}
            self.add(
                "trend_change",
                "normal",
                {
                    "id": f"Apakah berita tentang {k} minggu lalu naik atau turun dibanding minggu "
                    f"sebelumnya? "
                    "Berapa persen?",
                    "en": f"Did news about {kw2(k, 'en')} go up or down last week compared with the "
                    f"week before? "
                    "By how many percent?",
                },
                [c],
                gold_trend(self.call(c)),
                "trend_change",
                best,
                ["news_recap"],
            )
        for k, (a, b) in zip(
            self.rng.sample(pool, 3),
            [(SEP1, SEP30), self.span(SEP1, OCT14, 7, 21), self.span(SEP1, OCT14, 7, 21)],
            strict=True,
        ):
            c = {"tool": "entity_trend", "keyword": k, **self.dates(a, b)}
            self.add(
                "trend_peak",
                "normal",
                {
                    "id": f"Kapan puncak berita tentang {k} {period_text(a, b, 'id')}, dan berapa "
                    f"jumlahnya hari itu?",
                    "en": f"When did news about {kw2(k, 'en')} peak {period_text(a, b, 'en')}, and how many "
                    "headlines were there that day?",
                },
                [c],
                gold_trend(self.call(c)),
                "peak",
                best,
                ["news_recap"],
            )
        for k, args, pid, pen in [
            (self.pick(pool), {"period": "minggu lalu"}, "minggu lalu", "last week"),
            (
                self.pick(pool),
                self.dates(OCT1, OCT14),
                period_text(OCT1, OCT14, "id"),
                period_text(OCT1, OCT14, "en"),
            ),
            (self.pick(pool), self.dates(SEP1, SEP30), "bulan lalu", "last month"),
            (self.pick(pool), {"period": "kemarin"}, "kemarin", "yesterday"),
        ]:
            if pid == "bulan lalu":
                args = {"period": "bulan lalu"}
            c = {"tool": "entity_trend", "keyword": k, **args}
            r = self.call(c)
            g = gold_trend(r)
            g["story_urls"] = self.story_urls(c, r)
            self.add(
                "trend_stories",
                "normal",
                {
                    "id": f"Apa berita utama tentang {k} {pid}? Sebutkan 3 berita yang paling "
                    f"banyak dimuat media, "
                    "dengan link-nya.",
                    "en": f"What were the main stories about {kw2(k, 'en')} {pen}? Give the 3 "
                    f"stories carried by "
                    "the most outlets, with links.",
                },
                [c],
                g,
                "top_stories",
                best,
            )
        for k, (a, b) in [
            ("krakatau", (SEP1, date(2026, 9, 14))),
            (self.pick(pool), (SEP1, date(2026, 9, 10))),
        ]:
            c = {"tool": "entity_trend", "keyword": k, **self.dates(a, b)}
            r = self.call(c)
            assert r["change"] is None
            self.add(
                "trend_no_previous",
                "normal",
                {
                    "id": change_q[0].format(k=k, p=period_text(a, b, "id")),
                    "en": change_q[1].format(k=kw2(k, "en"), p=period_text(a, b, "en")),
                },
                [c],
                gold_trend(r),
                "trend_no_previous",
                best,
                ["news_recap"],
            )
        for k, args, pid, pen in [
            (
                "prabowo",
                self.dates(date(2026, 10, 15), date(2026, 10, 28)),
                "dari 15 sampai 28 Oktober 2026",
                "from 15 to 28 October 2026",
            ),
            ("kpk", {"period": "minggu ini"}, "minggu ini", "this week"),
        ]:
            self.abstain(
                "trend_abstain",
                "entity_trend",
                {"keyword": k, **args},
                {
                    "id": f"Bagaimana tren berita tentang {k} {pid}?",
                    "en": f"What is the trend of news about {kw2(k, 'en')} {pen}?",
                },
                best,
                ["news_recap"],
            )
        c = {"tool": "entity_trend", "keyword": NONSENSE, "period": "minggu lalu"}
        r = self.call(c)
        self.add(
            "trend_nonsense",
            "normal",
            {
                "id": f"Bagaimana tren berita tentang {NONSENSE} minggu lalu?",
                "en": f'What is the trend of news about "{NONSENSE}" last week?',
            },
            [c],
            {"total": r["total"], "start": r["start_date"], "end": r["end_date"]},
            "count",
            best,
            ["news_recap"],
        )

    def story_urls(self, call, r):
        """URLs of every headline in each of the top 3 stories (any of them counts as giving that story)."""
        a, b = date.fromisoformat(r["start_date"]), date.fromisoformat(r["end_date"])
        posts = [
            p for p in self.adapter.search(call["keyword"], a, b, today=self.today) if p.platform == "news"
        ]
        groups = group_stories(posts, self.opts["trend"]["settings"]["story_similarity"])
        by_url = {g[0].url: g for g in groups}
        return [[p.url for p in by_url[s["url"]]] for s in r["top_stories"][:3]]

    def traps(self):
        """Questions with words like naik / lonjakan / tren / bandingkan whose right tool is an old one."""

        def recap(k, args, tool="news_recap"):
            c = {"tool": tool, "keyword": k, **args}
            return c, self.call(c)

        def count_gold(r):
            return {
                "total": r["total"],
                "start": r["start_date"],
                "end": r["end_date"],
                "note": "a longer keyword may also be right; read manually if the total differs",
            }

        def sent_gold(r):
            st = r["sentiment_total"]
            return {
                "total": r["total"],
                "counts": st["counts"],
                "pct": st["pct"],
                "start": r["start_date"],
                "end": r["end_date"],
            }

        d1, d2 = date(2026, 10, 1), date(2026, 10, 7)
        c, r = recap("harga cabai", self.dates(d1, d2))
        self.add(
            "trap_count",
            "trap",
            {
                "id": f"Berapa jumlah berita tentang lonjakan harga cabai {span_text(d1, d2, 'id')}?",
                "en": f"How many news headlines about the jump in chilli prices (harga cabai) "
                f"were there {span_text(d1, d2, 'en')}?",
            },
            [c],
            count_gold(r),
            "count",
            ["news_recap"],
        )
        c, r = recap("pajak", {"period": "minggu lalu"})
        self.add(
            "trap_count",
            "trap",
            {
                "id": "Berapa jumlah berita tentang kenaikan pajak minggu lalu?",
                "en": "How many news headlines about tax increases (pajak) were there last week?",
            },
            [c],
            count_gold(r),
            "count",
            ["news_recap"],
        )
        c, r = recap("saham", {"period": "kemarin"})
        self.add(
            "trap_count",
            "trap",
            {
                "id": "Berapa jumlah berita tentang saham yang naik kemarin?",
                "en": "How many news headlines about stocks (saham) going up were there yesterday?",
            },
            [c],
            count_gold(r),
            "count",
            ["news_recap"],
        )
        d1, d2 = date(2026, 10, 1), date(2026, 10, 10)
        c, r = recap("dbd", self.dates(d1, d2))
        self.add(
            "trap_count",
            "trap",
            {
                "id": f"Berapa jumlah berita tentang lonjakan kasus DBD {span_text(d1, d2, 'id')}?",
                "en": f"How many news headlines about the rise in dengue (DBD) cases were there "
                f"{span_text(d1, d2, 'en')}?",
            },
            [c],
            count_gold(r),
            "count",
            ["news_recap"],
        )
        c, r = recap("pendidikan", {"period": "minggu lalu"})
        self.add(
            "trap_sentiment",
            "trap",
            {
                "id": "Bandingkan jumlah berita positif dan negatif tentang pendidikan minggu lalu.",
                "en": "Compare the number of positive and negative news headlines about education "
                "(pendidikan) "
                "last week.",
            },
            [c],
            sent_gold(r),
            "sentiment",
            ["news_recap"],
        )
        d1, d2 = date(2026, 10, 8), date(2026, 10, 12)
        c, r = recap("korupsi", self.dates(d1, d2))
        self.add(
            "trap_sentiment",
            "trap",
            {
                "id": f"Bagaimana perbandingan sentimen positif dan negatif berita tentang korupsi "
                f"{span_text(d1, d2, 'id')}?",
                "en": f"How do positive and negative sentiment compare in news about corruption (korupsi) "
                f"{span_text(d1, d2, 'en')}?",
            },
            [c],
            sent_gold(r),
            "sentiment",
            ["news_recap"],
        )
        c, r = recap("mobil listrik", {"period": "minggu lalu"})
        story = stories(r["samples"])
        picked, seen = [], set()
        for s in r["samples"]:
            if story[s["url"]] not in seen and len(picked) < 3:
                seen.add(story[s["url"]])
                picked.append({"headline": s["text"], "url": s["url"]})
        self.add(
            "trap_links",
            "trap",
            {
                "id": "Berikan 3 contoh judul berita tentang tren mobil listrik minggu lalu, "
                "lengkap dengan link-nya.",
                "en": "Give 3 example news headlines about the electric car (mobil listrik) trend "
                "last week, with "
                "their links.",
            },
            [c],
            {
                "total": r["total"],
                "sample_urls": [s["url"] for s in r["samples"]],
                "stories": story,
                "examples": picked,
                "links_needed": len(picked),
                "start": r["start_date"],
                "end": r["end_date"],
            },
            "links",
            ["news_recap"],
        )
        self.abstain(
            "trap_social",
            "social_recap",
            {"keyword": "harga beras", "period": "minggu lalu"},
            {
                "id": "Apa kata orang di media sosial tentang lonjakan harga beras minggu lalu?",
                "en": "What did people say on social media about the jump in rice prices (harga "
                "beras) last week?",
            },
            ["social_recap"],
        )
        self.items[-1]["kind"] = self.items[-2]["kind"] = "trap"

    def build(self):
        with open(self.v11_path, encoding="utf-8") as f:
            v11 = [json.loads(line) for line in f if line.strip()]
        out = [self.regold(i) for i in v11]
        self.spikes()
        self.compares()
        self.trends()
        self.traps()
        return out + self.items


def build(config_path, v11_path=V11, check_sha=True):
    b = BuilderV2(load_config(config_path), v11_path, check_sha)
    return b.build(), b


def dumps(items):
    return "".join(json.dumps(i, ensure_ascii=False, sort_keys=True) + "\n" for i in items)


def meta(items, builder):
    new = [i for i in items if i["origin"] == "v2"]
    return {
        "version": VERSION,
        "data_release": DATA_RELEASE,
        "data_sha256": DATA_SHA256,
        "seed": SEED,
        "today": builder.config.today,
        "items": len(items),
        "items_v11": len(items) - len(new),
        "items_new": len(new),
        "v11_gold_changed": [{"id": i, "note": n} for i, n in builder.changes],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="build benchmark v2")
    ap.add_argument("--config", default="config.snapshot-s1.example.yaml")
    ap.add_argument("--out", default=f"benchmark/questions-{VERSION}.jsonl")
    args = ap.parse_args(argv)
    items, builder = build(args.config)
    Path(args.out).write_text(dumps(items), encoding="utf-8")
    Path(args.out).with_suffix(".meta.json").write_text(
        json.dumps(meta(items, builder), ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    new = sum(i["origin"] == "v2" for i in items)
    print(f"{len(items)} items ({len(items) - new} from v1.1, {new} new) -> {args.out}")


if __name__ == "__main__":
    main()
