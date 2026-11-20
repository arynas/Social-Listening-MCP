"""Spike detection: pure functions that find unusual jumps in the number of posts about a keyword.

Everything is computed by code; no model is involved. detect_spikes has the same shape as news_recap
(period words, strict data range, must_tell_user first), so the MCP tool only has to expose it.
Method (docs/spikes.md):
- Count the posts per WIB day, or per WIB hour when the asked range has 3 days or fewer.
- For each point, the baseline is the median of the previous `window` points (trailing; days before the
  asked range are used when they are inside the data). Spread = 1.4826 x MAD of those points; if the MAD
  is 0, spread = sqrt(median), at least 1.
- Robust z = (count - median) / spread. A point is a spike when z >= z_threshold, count >= min_count and
  count / median >= min_ratio (the ratio rule passes when the median is 0).
- Keywords match as whole words (case-insensitive; phrases allowed): "ott" does not match "Baggott".
  This is stricter than the recap tools, which match any substring.
- The same rule on the number of negative posts gives negative_spikes (only for labelled data).
"""
import math
import re
from collections import Counter
from datetime import datetime, time, timedelta

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
    top,
)
from slmcp.schema import WIB

MAD_SCALE = 1.4826
HOURLY_MAX_DAYS = 3
MAX_SPIKES = 10
MAX_SERIES_POINTS = 72
SPIKE_SAMPLES = 3
MIN_BASELINE = 7      # fewer baseline points than this gives a warning
SCORE_MIN_BASELINE = 3  # a point needs at least this many baseline points to be scored at all
MAX_TOKENS = 2000
DEFAULTS = {"window_days": 7, "window_hours": 24, "z_threshold": 3.0, "min_count": 3, "min_ratio": 2.0}


def median(values):
    s = sorted(values)
    n = len(s)
    if not n:
        return 0.0
    return float(s[n // 2]) if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def robust_baseline(values):
    """Median and spread of counts: spread = 1.4826 x MAD, or sqrt(median) (at least 1) if MAD is 0."""
    m = median(values)
    mad = median([abs(v - m) for v in values])
    spread = MAD_SCALE * mad if mad else max(1.0, math.sqrt(m))
    return m, spread


def whole_word(keyword):
    """A case-insensitive pattern that matches keyword only as whole word(s)."""
    words = [re.escape(w) for w in str(keyword).lower().split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)")


def score_series(counts, window, z_threshold, min_count, first=0, min_ratio=0.0):
    """Score counts[first:] against a trailing window. Returns one dict per scored point:
    index, count, median, spread, z, baseline_points, spike (bool). Points with fewer than
    SCORE_MIN_BASELINE earlier points are not scored."""
    out = []
    for i in range(first, len(counts)):
        base = counts[max(0, i - window):i]
        if len(base) < SCORE_MIN_BASELINE:
            continue
        m, spread = robust_baseline(base)
        z = (counts[i] - m) / spread
        ratio_ok = m == 0 or counts[i] / m >= min_ratio
        out.append({"index": i, "count": counts[i], "median": m, "spread": spread, "z": z,
                    "baseline_points": len(base),
                    "spike": counts[i] >= min_count and z >= z_threshold and ratio_ok})
    return out


def slots(start, end, unit):
    """Labels of the WIB days or hours from start to end (both days included)."""
    if unit == "hour":
        t = datetime.combine(start, time(0), WIB)
        stop = datetime.combine(end + timedelta(days=1), time(0), WIB)
        out = []
        while t < stop:
            out.append(t.strftime("%Y-%m-%d %H:00"))
            t += timedelta(hours=1)
        return out
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def slot_of(post, unit):
    w = post.date_wib
    return w.strftime("%Y-%m-%d %H:00") if unit == "hour" else w.date().isoformat()


def choose_unit(start, end, unit="auto"):
    if unit in ("day", "hour"):
        return unit
    return "hour" if (end - start).days + 1 <= HOURLY_MAX_DAYS else "day"


def _entry(label, slot_posts, s, negative_count=None):
    entry = {"at": label, "count": s["count"], "baseline_median": round(s["median"], 1),
             "spread": round(s["spread"], 2), "z": round(s["z"], 1),
             "ratio_to_median": round(s["count"] / s["median"], 1) if s["median"] else None}
    if negative_count is not None:
        entry["negative"] = negative_count
    entry["top_sources"] = top(Counter(p.source for p in slot_posts), 3)
    entry["samples"] = samples(slot_posts, SPIKE_SAMPLES, text_chars=120)
    return entry


def spikes(posts, keyword, start, end, today, baseline_start=None, coverage=None, sentiment_labels=None,
           kind="news", unit="auto", settings=None):
    """Spike report for posts already filtered by keyword, from baseline_start (or start) to end.
    Pure: no I/O, no clock. settings: dict with window_days, window_hours, z_threshold, min_count."""
    cfg = {**DEFAULTS, **(settings or {})}
    start, end, today = parse_day(start, "start"), parse_day(end, "end"), parse_day(today, "today")
    baseline_start = parse_day(baseline_start, "baseline_start") if baseline_start else start
    unit = choose_unit(start, end, unit)
    window = cfg["window_hours"] if unit == "hour" else cfg["window_days"]
    labels = slots(baseline_start, end, unit)
    first = labels.index(slots(start, start, unit)[0])
    by_slot = {label: [] for label in labels}
    for p in posts:
        s = slot_of(p, unit)
        if s in by_slot:
            by_slot[s].append(p)
    counts = [len(by_slot[label]) for label in labels]
    labelled = any(p.sentiment for p in posts)
    neg = [sum(p.sentiment == "negative" for p in by_slot[label]) for label in labels]

    scored = score_series(counts, window, cfg["z_threshold"], cfg["min_count"], first, cfg["min_ratio"])
    found = sorted((s for s in scored if s["spike"]), key=lambda s: (-s["z"], s["index"]))
    volume = [_entry(labels[s["index"]], by_slot[labels[s["index"]]], s,
                     neg[s["index"]] if labelled else None) for s in found[:MAX_SPIKES]]
    neg_found = []
    if labelled:
        nscored = score_series(neg, window, cfg["z_threshold"], cfg["min_count"], first, cfg["min_ratio"])
        neg_found = sorted((s for s in nscored if s["spike"]), key=lambda s: (-s["z"], s["index"]))
    negative = []
    for s in neg_found[:MAX_SPIKES]:
        label = labels[s["index"]]
        entry = _entry(label, [p for p in by_slot[label] if p.sentiment == "negative"], s)
        entry["total_posts"] = counts[s["index"]]
        negative.append(entry)

    in_range = counts[first:]
    shortest = min((s["baseline_points"] for s in scored), default=0)
    out = {
        "keyword": keyword, "source": kind, "start_date": start.isoformat(), "end_date": end.isoformat(),
        "today": today.isoformat(), "today_weekday": WEEKDAYS_ID[today.weekday()],
        "relative_dates": relative_dates(today),
        "unit": unit, "points": len(in_range), "total": sum(in_range),
        "method": {"baseline": f"median of the previous {window} {unit}s", "spread": "1.4826 x MAD "
                   "(sqrt(median), at least 1, if MAD is 0)", "z_threshold": cfg["z_threshold"],
                   "min_count": cfg["min_count"], "min_ratio": cfg["min_ratio"],
                   "keyword_match": "whole word"},
        "unscored_points": len(in_range) - len(scored),
        "spike_count": len(found),
        "spikes": volume,
    }
    if labelled:
        out["negative_spike_count"] = len(neg_found)
        out["negative_spikes"] = negative
    if len(in_range) <= MAX_SERIES_POINTS:
        out["counts"] = dict(zip(labels[first:], in_range, strict=True))
    scored_idx = {s["index"] for s in scored}
    unscored = [labels[i] for i in range(first, len(labels)) if i not in scored_idx]
    out["data_warnings"] = warnings(in_range, scored, shortest, window, unit, coverage, sentiment_labels,
                                    kind, bool(negative), unscored)
    out = {"must_tell_user": must_tell_user(out["data_warnings"]), **out}
    return fit(out)


def warnings(in_range, scored, shortest, window, unit, coverage, sentiment_labels, kind, negative_shown,
             unscored=()):
    out = []
    if not sum(in_range):
        out.append("no posts found for this keyword and date range, so there are no spikes")
    if not scored:
        out.append(f"the baseline is too short: no {unit} had at least {SCORE_MIN_BASELINE} earlier {unit}s "
                   "of data, so nothing could be scored")
    elif unscored:
        shown = ", ".join(unscored[:5]) + (f" and {len(unscored) - 5} more" if len(unscored) > 5 else "")
        out.append(f"{len(unscored)} {unit}(s) could not be scored (fewer than {SCORE_MIN_BASELINE} earlier "
                   f"{unit}s of data): {shown}")
    if scored and shortest < MIN_BASELINE:
        out.append(f"the baseline is short ({shortest} {unit}s for the earliest points, fewer than "
                   f"{MIN_BASELINE}); treat early spikes as a rough hint")
    if negative_shown and sentiment_labels == "model":
        out.append("negative spikes use rough model sentiment labels (63% agreement with a person, "
                   "kappa 0.44); treat them as an estimate")
    if kind == "news":
        out.append("source is news headlines only (no article text, no social media)")
    out.append("a spike is a statistical signal, not proof of an event; check the sample headlines")
    return out


def fit(out, max_tokens=MAX_TOKENS):
    """Keep the output under max_tokens: fewer samples, then no per-point counts, then fewer spikes."""
    for key in ("negative_spikes", "spikes"):
        for entry in reversed(out.get(key, [])):
            while estimate_tokens(out) > max_tokens and len(entry["samples"]) > 1:
                entry["samples"].pop()
    if estimate_tokens(out) > max_tokens:
        out.pop("counts", None)
    for key in ("negative_spikes", "spikes"):
        while estimate_tokens(out) > max_tokens and len(out.get(key, [])) > 1:
            out[key].pop()
    out["output_tokens_estimate"] = 0
    out["output_tokens_estimate"] = estimate_tokens(out)
    return out


def detect_spikes(adapter, keyword, start_date=None, end_date=None, today=None, sentiment_labels=None,
                  period=None, source="news", unit="auto", settings=None):
    """Spikes for keyword in start_date..end_date (WIB, inclusive) or a period word; source: news/social."""
    today = parse_day(today, "today") if today else datetime.now(WIB).date()
    kind = "social" if source == "social" else "news"
    pick = (lambda p: p.platform == "news") if kind == "news" else (lambda p: p.platform != "news")
    keyword = " ".join(str(keyword).split())
    cfg = {**DEFAULTS, **(settings or {})}
    try:
        start_date, end_date, word = resolve_period(period, start_date, end_date, today)
        start, end = parse_day(start_date, "start_date"), parse_day(end_date, "end_date")
        if start > end:
            raise QueryError(f"start_date {start} is after end_date {end}; swap them")
        chosen = choose_unit(start, end, unit)
        lookback = (timedelta(hours=cfg["window_hours"]) if chosen == "hour"
                    else timedelta(days=cfg["window_days"]))
        baseline_start = (datetime.combine(start, time(0)) - lookback).date()
        if hasattr(adapter, "posts"):
            coverage = data_range(adapter.posts)
            if not any(pick(p) for p in adapter.posts):
                return no_data(keyword, start, end, today, None, word, kind)
            if start < coverage[0] or end > coverage[1]:
                return no_data(keyword, start, end, today, coverage, word, kind)
            baseline_start = max(baseline_start, coverage[0])
        else:
            coverage = None
        match = whole_word(keyword)
        found = [p for p in adapter.search(keyword, baseline_start, end, today=today)
                 if pick(p) and match.search(" ".join(p.text.lower().split()))]
    except QueryError as e:
        return {"error": str(e), "today": today.isoformat(), "today_weekday": WEEKDAYS_ID[today.weekday()]}
    out = spikes(found, keyword, start, end, today, baseline_start, coverage, sentiment_labels, kind, chosen,
                 cfg)
    out = {"must_tell_user": out.pop("must_tell_user"), "data_available": True, **out}
    if word:
        out["period"] = word
    return fit(out)


def from_config(config):
    """Keyword arguments for detect_spikes taken from a Config."""
    s = config.spikes
    return {"today": config.today, "sentiment_labels": config.data_source.sentiment_labels,
            "settings": {"window_days": s.window_days, "window_hours": s.window_hours,
                         "z_threshold": s.z_threshold, "min_count": s.min_count, "min_ratio": s.min_ratio}}


__all__ = ["detect_spikes", "whole_word", "spikes", "score_series", "robust_baseline", "median", "slots",
           "choose_unit", "from_config"]
