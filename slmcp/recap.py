"""Recap logic: pure functions that turn adapter output (a list of Post) into a small, sourced summary.

Everything here is computed by code; no model is involved. news_recap and social_recap are thin
wrappers with the shape of the future MCP tools (S0-5b only has to expose them).

Contract (see docs/recap.md):
- Days and weeks are WIB (Asia/Jakarta). A week is Monday to Sunday.
- Percentages have one decimal and always add up to exactly 100.0 (largest-remainder rounding).
- Posts without a sentiment label are counted as "unlabelled" and shown in the percentages.
- Ties in top lists are broken by name (A to Z), so the output is the same on every run.
- The output is kept under about 2,000 tokens, estimated as characters / 4 of the compact JSON.
"""
import json
from collections import Counter
from datetime import datetime, timedelta

from slmcp.adapters.base import QueryError, parse_day
from slmcp.schema import WIB

SENTIMENTS = ("positive", "negative", "neutral", "unlabelled")
WEEKDAYS_ID = ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")
WEEKDAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS_EN = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
             "October", "November", "December")
MAX_TREND_POINTS = 30
TOP_N = 5
MAX_SAMPLES = 10
MAX_TOKENS = 2000
CHARS_PER_TOKEN = 4
SAMPLE_TEXT_CHARS = 160


def estimate_tokens(obj):
    """Rough token count without a model: characters of the compact JSON divided by 4."""
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":"))) // CHARS_PER_TOKEN


def percentages(counts, keys=SENTIMENTS):
    """Percent of each key with one decimal, adding up to exactly 100.0 (largest remainder).
    Ties in the remainder go to the key that comes first in keys."""
    total = sum(counts.get(k, 0) for k in keys)
    if not total:
        return {k: 0.0 for k in keys}
    tenths = {k: counts.get(k, 0) * 1000 / total for k in keys}
    base = {k: int(v) for k, v in tenths.items()}
    left = 1000 - sum(base.values())
    order = sorted(keys, key=lambda k: (-(tenths[k] - base[k]), keys.index(k)))
    for k in order[:left]:
        base[k] += 1
    return {k: base[k] / 10 for k in keys}


def sentiment_total(posts):
    counts = Counter(p.sentiment or "unlabelled" for p in posts)
    return {"counts": {k: counts.get(k, 0) for k in SENTIMENTS}, "pct": percentages(counts)}


def relative_dates(today):
    """The exact WIB dates of common Indonesian time phrases, from today."""
    monday = today - timedelta(days=today.weekday())
    first = today.replace(day=1)
    last_month_end = first - timedelta(days=1)
    spans = {
        "hari ini": (today, today),
        "kemarin": (today - timedelta(days=1), today - timedelta(days=1)),
        "minggu ini": (monday, today),
        "minggu lalu": (monday - timedelta(days=7), monday - timedelta(days=1)),
        "7 hari terakhir": (today - timedelta(days=6), today),
        "30 hari terakhir": (today - timedelta(days=29), today),
        "bulan ini": (first, today),
        "bulan lalu": (last_month_end.replace(day=1), last_month_end),
    }
    return {k: [a.isoformat(), b.isoformat()] for k, (a, b) in spans.items()}


def trend(posts, start, end):
    """Counts per WIB day (every day in the range, also empty ones), or per week (Monday to Sunday)
    when the range has more than 30 days. Each point: period start, end, total and sentiment counts."""
    days = (end - start).days + 1
    weekly = days > MAX_TREND_POINTS
    buckets = []
    d = start
    while d <= end:
        stop = min(end, d + timedelta(days=6 - d.weekday())) if weekly else d
        buckets.append([d, stop, Counter()])
        d = stop + timedelta(days=1)
    index = {}
    for i, (a, b, _) in enumerate(buckets):
        x = a
        while x <= b:
            index[x] = i
            x += timedelta(days=1)
    for p in posts:
        i = index.get(p.date_wib.date())
        if i is not None:
            buckets[i][2][p.sentiment or "unlabelled"] += 1
    points = [{"start": a.isoformat(), "end": b.isoformat(), "total": sum(c.values()),
               **{k: c.get(k, 0) for k in SENTIMENTS}} for a, b, c in buckets]
    return {"unit": "week" if weekly else "day", "points": points}


def top(counter, n=TOP_N):
    """The n most common items; ties broken by name (A to Z)."""
    return [{"name": k, "count": v} for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def _sample(p, text_chars=SAMPLE_TEXT_CHARS):
    text = p.text if len(p.text) <= text_chars else p.text[:text_chars - 1].rstrip() + "…"
    out = {"date": p.date_wib.strftime("%Y-%m-%d %H:%M"), "source": p.source, "platform": p.platform,
           "text": text, "url": p.url, "sentiment": p.sentiment}
    if p.engagement is not None:
        out["engagement"] = p.engagement
    return out


def samples(posts, n=MAX_SAMPLES, by_engagement=False, text_chars=SAMPLE_TEXT_CHARS):
    """Up to n example posts. Social: highest engagement first. News: spread evenly over time.
    Links come only from the posts themselves."""
    if not posts:
        return []
    if by_engagement:
        chosen = sorted(posts, key=lambda p: (-(p.engagement or 0), p.date, p.url))[:n]
    else:
        ordered = sorted(posts, key=lambda p: (p.date, p.url))
        if len(ordered) <= n:
            chosen = ordered
        else:
            step = (len(ordered) - 1) / (n - 1)
            chosen = [ordered[round(i * step)] for i in range(n)]
    return [_sample(p, text_chars) for p in chosen]


def data_range(all_posts):
    """First and last WIB day in the data, or None for empty data."""
    if not all_posts:
        return None
    days = [p.date_wib.date() for p in all_posts]
    return min(days), max(days)


def warnings(posts, start, end, today, coverage, sentiment_labels, kind):
    out = []
    if not posts:
        out.append("no posts found for this keyword and date range")
    if coverage:
        first, last = coverage
        if end < first or start > last:
            out.append(f"the range {start}..{end} is outside the data, which covers {first}..{last}")
        elif start < first or end > last:
            out.append(f"the range {start}..{end} is only partly covered: the data covers {first}..{last}")
    if end > today:
        out.append(f"the range ends after today ({today}); there is no data for future dates")
    labelled = [p for p in posts if p.sentiment]
    if labelled and sentiment_labels == "model":
        out.append("sentiment labels are rough model output, not checked by a person; "
                   "treat the percentages as an estimate")
    unlabelled = len(posts) - len(labelled)
    if unlabelled:
        out.append(f"{unlabelled} posts have no sentiment label (counted as unlabelled)")
    if kind == "news":
        out.append("source is news headlines only (no article text, no social media)")
    if kind == "social" and coverage and not posts:
        out.append("this data may have no social media posts; social results need a social data source")
    return out


def must_tell_user(data_warnings):
    """The warnings as plain sentences for the user (capital letter, full stop)."""
    return [w[:1].upper() + w[1:] + ("" if w.endswith(".") else ".") for w in data_warnings]


def today_line(today):
    """One sentence for the tool descriptions: the date to use instead of the model's own clock,
    with the exact dates of the most common Indonesian time words."""
    today = parse_day(today, "today")
    r = relative_dates(today)
    span = lambda k: r[k][0] if r[k][0] == r[k][1] else f"{r[k][0]}..{r[k][1]}"  # noqa: E731
    return (f"Today is {WEEKDAYS_EN[today.weekday()]} {today.day} {MONTHS_EN[today.month - 1]} "
            f"{today.year} (WIB). Use this date, not your own clock: kemarin = {span('kemarin')}, "
            f"minggu ini = {span('minggu ini')}, minggu lalu = {span('minggu lalu')}, "
            f"bulan ini = {span('bulan ini')}.")


def recap(posts, keyword, start, end, today, coverage=None, sentiment_labels=None, kind="news",
          max_tokens=MAX_TOKENS):
    """The full summary for posts already filtered by keyword and range. Pure: no I/O, no clock."""
    start, end, today = parse_day(start, "start"), parse_day(end, "end"), parse_day(today, "today")
    social = kind == "social"
    out = {
        "keyword": keyword, "start_date": start.isoformat(), "end_date": end.isoformat(),
        "today": today.isoformat(), "today_weekday": WEEKDAYS_ID[today.weekday()],
        "relative_dates": relative_dates(today),
        "total": len(posts),
        "sentiment_total": sentiment_total(posts),
        "trend": trend(posts, start, end),
        "top_sources": top(Counter(p.source for p in posts)),
        "top_platforms": top(Counter(p.platform for p in posts)),
    }
    if social:
        out["top_posts"] = samples(posts, TOP_N, by_engagement=True)
    out["samples"] = samples(posts, MAX_SAMPLES, by_engagement=social)
    out["data_warnings"] = warnings(posts, start, end, today, coverage, sentiment_labels, kind)
    # must_tell_user goes first, so a model reading the result sees it before the numbers
    out = {"must_tell_user": must_tell_user(out["data_warnings"]), **out}
    return fit(out, max_tokens)


def fit(out, max_tokens=MAX_TOKENS):
    """Shrink the least important parts until the estimate is under max_tokens: shorter sample texts,
    then fewer samples, then fewer top posts. Counts, sentiment and trend are never cut."""
    for text_chars in (SAMPLE_TEXT_CHARS, 100, 60):
        if estimate_tokens(out) <= max_tokens:
            break
        for key in ("samples", "top_posts"):
            for s in out.get(key, []):
                if len(s["text"]) > text_chars:
                    s["text"] = s["text"][:text_chars - 1].rstrip() + "…"
    while estimate_tokens(out) > max_tokens and out.get("samples"):
        out["samples"].pop()
    while estimate_tokens(out) > max_tokens and out.get("top_posts"):
        out["top_posts"].pop()
    # measured on the final output, including this field itself
    out["output_tokens_estimate"] = 0
    out["output_tokens_estimate"] = estimate_tokens(out)
    return out


def _run(adapter, keyword, start_date, end_date, today, sentiment_labels, kind):
    today = parse_day(today, "today") if today else datetime.now(WIB).date()
    pick = (lambda p: p.platform == "news") if kind == "news" else (lambda p: p.platform != "news")
    try:
        found = [p for p in adapter.search(keyword, start_date, end_date, today=today) if pick(p)]
    except QueryError as e:
        return {"error": str(e), "today": today.isoformat(), "today_weekday": WEEKDAYS_ID[today.weekday()],
                "relative_dates": relative_dates(today)}
    coverage = data_range([p for p in getattr(adapter, "posts", []) if pick(p)])
    return recap(found, " ".join(str(keyword).split()), start_date, end_date, today, coverage,
                 sentiment_labels, kind)


def news_recap(adapter, keyword, start_date, end_date, today=None, sentiment_labels=None):
    """Recap of news posts (platform "news") for keyword in start_date..end_date (WIB, inclusive).
    today: frozen date for a fixed snapshot (e.g. 2026-10-15); default is the real date in WIB.
    sentiment_labels: "model" adds a warning that the labels are rough model output."""
    return _run(adapter, keyword, start_date, end_date, today, sentiment_labels, "news")


def social_recap(adapter, keyword, start_date, end_date, today=None, sentiment_labels=None):
    """Recap of social media posts (every platform except "news"), with top posts by engagement."""
    return _run(adapter, keyword, start_date, end_date, today, sentiment_labels, "social")


def from_config(config):
    """Keyword arguments for news_recap / social_recap taken from a Config (today, sentiment_labels)."""
    return {"today": config.today, "sentiment_labels": config.data_source.sentiment_labels}


__all__ = ["recap", "news_recap", "social_recap", "from_config", "relative_dates", "percentages",
           "sentiment_total", "must_tell_user", "today_line", "trend", "top", "samples", "data_range",
           "estimate_tokens"]
