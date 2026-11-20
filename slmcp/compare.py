"""Keyword comparison: pure functions that put 2 or 3 keywords side by side for the same period.

Everything is computed by code; no model is involved. compare_keywords has the same shape as news_recap
(period words, strict data range, must_tell_user first), so the MCP tool only has to expose it.
Method (docs/compare.md):
- Each keyword is searched with the same shared matcher as news_recap / social_recap (adapter.search,
  substring, case-insensitive), so each keyword's total equals what news_recap gives for it.
  detect_spikes matches whole words instead; see docs/compare.md for why this tool does not.
- Per keyword: total, sentiment_total (counts and percent, adding up to 100.0), up to 3 sample headlines.
- Trend side by side per WIB day, or per WIB hour when the range has 3 days or fewer (as detect_spikes).
- Per pair: difference and ratio of totals and of the negative share, and on how many days each is higher.
  A ratio that would divide by 0 is null.
- Overlap: headlines that match 2 or more keywords. They count in each keyword's total, so the totals
  are not distinct headlines; distinct_total says how many distinct headlines there are.
"""
from collections import Counter
from datetime import datetime
from itertools import combinations

from slmcp.adapters.base import QueryError, parse_day
from slmcp.recap import (
    WEEKDAYS_ID,
    data_range,
    estimate_tokens,
    must_tell_user,
    no_data,
    relative_dates,
    resolve_period,
    samples,
    sentiment_total,
)
from slmcp.schema import WIB
from slmcp.spikes import choose_unit, slot_of, slots

MIN_KEYWORDS = 2
MAX_KEYWORDS = 3
SMALL_COUNT = 10
COMPARE_SAMPLES = 3
SAMPLE_TEXT_CHARS = 120
MAX_TOKENS = 2000


def clean_keywords(keywords):
    """A list of 2 or 3 distinct keywords (spaces normalised). A string is split on commas.
    Raises QueryError with a short message."""
    if isinstance(keywords, str):
        keywords = keywords.split(",")
    cleaned = [" ".join(str(k or "").split()) for k in (keywords or [])]
    cleaned = [k for k in cleaned if k]
    if not MIN_KEYWORDS <= len(cleaned) <= MAX_KEYWORDS:
        raise QueryError(f"keywords: give {MIN_KEYWORDS} or {MAX_KEYWORDS} keywords to compare, got "
                         f"{len(cleaned)}; for one keyword use news_recap")
    lowered = [k.lower() for k in cleaned]
    if len(set(lowered)) != len(lowered):
        raise QueryError("keywords: the same keyword is given twice; give different keywords")
    return cleaned


def _key(p):
    return (p.url, p.date, p.text)


def ratio(a, b, digits=2):
    """a / b rounded, or None when b is 0."""
    return round(a / b, digits) if b else None


def compare(found, start, end, today, coverage=None, sentiment_labels=None, kind="news", unit="auto",
            max_tokens=MAX_TOKENS):
    """The comparison for posts already found per keyword ({keyword: [Post]}, in the asked order).
    Pure: no I/O, no clock."""
    start, end, today = parse_day(start, "start"), parse_day(end, "end"), parse_day(today, "today")
    unit = choose_unit(start, end, unit)
    labels = slots(start, end, unit)
    names = list(found)
    series = {}
    for k in names:
        c = Counter(slot_of(p, unit) for p in found[k])
        series[k] = [c.get(label, 0) for label in labels]

    per = []
    for k in names:
        st = sentiment_total(found[k])
        per.append({"keyword": k, "total": len(found[k]), "sentiment_total": st,
                    "negative_share": st["pct"]["negative"],
                    "samples": samples(found[k], COMPARE_SAMPLES, text_chars=SAMPLE_TEXT_CHARS)})
    by_name = {e["keyword"]: e for e in per}

    pairs = []
    for a, b in combinations(names, 2):
        ta, tb = by_name[a]["total"], by_name[b]["total"]
        na, nb = by_name[a]["negative_share"], by_name[b]["negative_share"]
        higher = Counter("a" if x > y else "b" if y > x else "tie"
                         for x, y in zip(series[a], series[b], strict=True))
        pairs.append({"a": a, "b": b, "total_diff": ta - tb, "total_ratio": ratio(ta, tb),
                      "negative_share_diff": round(na - nb, 1) if ta and tb else None,
                      "negative_share_ratio": ratio(na, nb) if ta and tb else None,
                      f"{unit}s_a_higher": higher["a"], f"{unit}s_b_higher": higher["b"],
                      f"{unit}s_tied": higher["tie"]})

    hits = Counter()
    pair_overlap = Counter()
    for k in names:
        for key in {_key(p) for p in found[k]}:
            hits[key] += 1
    keysets = {k: {_key(p) for p in found[k]} for k in names}
    for a, b in combinations(names, 2):
        pair_overlap[(a, b)] = len(keysets[a] & keysets[b])
    overlap = {"headlines_in_2_or_more": sum(1 for n in hits.values() if n >= 2),
               "distinct_total": len(hits),
               "pairs": [{"a": a, "b": b, "count": pair_overlap[(a, b)]} for a, b in combinations(names, 2)]}
    if len(names) == 3:
        overlap["in_all_three"] = sum(1 for n in hits.values() if n == 3)

    out = {
        "keywords": names, "source": kind, "start_date": start.isoformat(), "end_date": end.isoformat(),
        "today": today.isoformat(), "today_weekday": WEEKDAYS_ID[today.weekday()],
        "relative_dates": relative_dates(today),
        "method": {"keyword_match": "substring, same as news_recap", "trend_unit": unit},
        "per_keyword": per,
        "pairs": pairs,
        "overlap": overlap,
        "trend": {"unit": unit, "labels": labels, "counts": series},
    }
    out["data_warnings"] = warnings(per, overlap, coverage, sentiment_labels, kind, start, end)
    out = {"must_tell_user": must_tell_user(out["data_warnings"]), **out}
    return fit(out, max_tokens)


def warnings(per, overlap, coverage, sentiment_labels, kind, start, end):
    out = []
    for e in per:
        if not e["total"]:
            out.append(f"'{e['keyword']}' has 0 headlines in this period, so ratios that divide by it are "
                       "not given (null)")
        elif e["total"] < SMALL_COUNT:
            out.append(f"'{e['keyword']}' has only {e['total']} headlines (fewer than {SMALL_COUNT}); "
                       "differences and ratios with it are unreliable")
    if overlap["headlines_in_2_or_more"]:
        n, distinct = overlap["headlines_in_2_or_more"], overlap["distinct_total"]
        out.append(f"{n} headlines match more than one keyword and are counted in each of their totals; "
                   f"there are {distinct} distinct headlines")
    if sentiment_labels == "model" and any(e["sentiment_total"]["counts"]["unlabelled"] < e["total"]
                                           for e in per):
        out.append("sentiment labels are rough model output (63% agreement with a person, kappa 0.44); "
                   "treat the shares and their differences as an estimate")
    unlabelled = sum(e["sentiment_total"]["counts"]["unlabelled"] for e in per)
    if unlabelled:
        out.append(f"{unlabelled} matching posts have no sentiment label (counted as unlabelled)")
    if kind == "news":
        out.append("source is news headlines only (no article text, no social media)")
    return out


def fit(out, max_tokens=MAX_TOKENS):
    """Keep the output under max_tokens: shorter sample texts, then fewer samples, then no trend table
    (with a warning). Totals, sentiment, pairs and overlap are never cut."""
    for chars in (80, 50):
        if estimate_tokens(out) <= max_tokens:
            break
        for e in out.get("per_keyword", []):
            for s in e["samples"]:
                if len(s["text"]) > chars:
                    s["text"] = s["text"][:chars - 1].rstrip() + "…"
    for keep in (2, 1):
        if estimate_tokens(out) <= max_tokens:
            break
        for e in out.get("per_keyword", []):
            del e["samples"][keep:]
    if estimate_tokens(out) > max_tokens and "counts" in out.get("trend", {}):
        n = len(out["trend"]["labels"])
        out["trend"] = {"unit": out["trend"]["unit"], "removed": True}
        u = out["trend"]["unit"]
        line = (f"the {u}-by-{u} table ({n} points) was left out to keep the result short; "
                "ask for a shorter period to see it")
        out["data_warnings"].append(line)
        out["must_tell_user"] = must_tell_user(out["data_warnings"])
    out["output_tokens_estimate"] = 0
    out["output_tokens_estimate"] = estimate_tokens(out)
    return out


def compare_keywords(adapter, keywords, start_date=None, end_date=None, today=None, sentiment_labels=None,
                     period=None, source="news", unit="auto"):
    """Compare 2 or 3 keywords over start_date..end_date (WIB, inclusive) or a period word.
    source: news (platform "news", default) or social (every other platform)."""
    today = parse_day(today, "today") if today else datetime.now(WIB).date()
    kind = "social" if source == "social" else "news"
    pick = (lambda p: p.platform == "news") if kind == "news" else (lambda p: p.platform != "news")
    try:
        names = clean_keywords(keywords)
        start_date, end_date, word = resolve_period(period, start_date, end_date, today)
        start, end = parse_day(start_date, "start_date"), parse_day(end_date, "end_date")
        if start > end:
            raise QueryError(f"start_date {start} is after end_date {end}; swap them")
        if hasattr(adapter, "posts"):
            coverage = data_range(adapter.posts)
            if not any(pick(p) for p in adapter.posts):
                return _no_data(names, start, end, today, None, word, kind)
            if start < coverage[0] or end > coverage[1]:
                return _no_data(names, start, end, today, coverage, word, kind)
        else:
            coverage = None
        found = {k: [p for p in adapter.search(k, start, end, today=today) if pick(p)] for k in names}
    except QueryError as e:
        return {"error": str(e), "today": today.isoformat(), "today_weekday": WEEKDAYS_ID[today.weekday()]}
    out = compare(found, start, end, today, coverage, sentiment_labels, kind, unit)
    out = {"must_tell_user": out.pop("must_tell_user"), "data_available": True, **out}
    if word:
        out["period"] = word
    return fit(out)


def _no_data(names, start, end, today, coverage, word, kind):
    out = no_data(", ".join(names), start, end, today, coverage, word, kind)
    out.pop("keyword")
    return {"must_tell_user": out.pop("must_tell_user"), "data_available": False, "keywords": names,
            **{k: v for k, v in out.items() if k != "data_available"}}


def from_config(config):
    """Keyword arguments for compare_keywords taken from a Config (today, sentiment_labels)."""
    return {"today": config.today, "sentiment_labels": config.data_source.sentiment_labels}


__all__ = ["compare_keywords", "compare", "clean_keywords", "ratio", "from_config"]
