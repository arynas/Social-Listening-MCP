"""Topic trend: pure functions for one keyword (a person, place or topic) over a period.

Everything is computed by code; no model is involved. entity_trend has the same shape as news_recap
(period words, strict data range, must_tell_user first), so the MCP tool only has to expose it.
Method (docs/trend.md):
- Same shared matcher as news_recap (adapter.search, substring), so the total equals news_recap's.
- Counts and sentiment counts per WIB day, or per WIB hour when the range has 3 days or fewer.
- Change against the previous period of the same length (the days just before). If that period is not
  fully inside the data, no change numbers are given (no data = no numbers).
- Top stories (top_posts): headlines of the same story are grouped (same or next day, word overlap at
  or above story_similarity after removing site names and punctuation) and ranked by the number of
  distinct sources that carried the story. Posts with engagement (social data) are ranked by engagement.
"""
import re
from collections import Counter
from datetime import datetime, timedelta

from slmcp.adapters.base import QueryError, parse_day
from slmcp.recap import (
    SENTIMENTS,
    WEEKDAYS_ID,
    data_range,
    estimate_tokens,
    human_range,
    must_tell_user,
    no_data,
    percentages,
    relative_dates,
    resolve_period,
)
from slmcp.schema import WIB
from slmcp.spikes import choose_unit, slot_of, slots

TOP_STORIES = 5
SMALL_COUNT = 10
STORY_TEXT_CHARS = 120
MAX_TOKENS = 2000
DEFAULTS = {"story_similarity": 0.5}
SEPARATORS = re.compile(r"\s+(?:[-|:–—])\s+")
SITE_WORDS_MAX = 3  # a last part of at most this many words after " - ", " | ", " : " is a site name


def normalise(text):
    """Lowercase words of a headline without a trailing site name (" - Kompas", " : Okezone News")
    and without punctuation."""
    parts = SEPARATORS.split(" ".join(str(text).split()))
    if len(parts) > 1 and len(parts[-1].split()) <= SITE_WORDS_MAX:
        parts = parts[:-1]
    return re.findall(r"\w+", " ".join(parts).lower())


def jaccard(a, b):
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a or b else 0.0


def _story_entry(p, sources, headlines):
    text = p.text if len(p.text) <= STORY_TEXT_CHARS else p.text[:STORY_TEXT_CHARS - 1].rstrip() + "…"
    return {"date": p.date_wib.strftime("%Y-%m-%d %H:%M"), "text": text, "url": p.url, "source": p.source,
            "sources": sources, "headlines": headlines}


def group_stories(posts, similarity=DEFAULTS["story_similarity"]):
    """Groups of posts about the same story: a post joins the first group whose first headline is on the
    same or the previous day and has a word overlap (Jaccard) of at least similarity. Date order."""
    groups = []  # each: [first_day, last_day, words of the first headline, posts]
    for p in sorted(posts, key=lambda p: (p.date, p.url)):
        day, words = p.date_wib.date(), normalise(p.text)
        for g in groups:
            if day - g[1] <= timedelta(days=1) and jaccard(words, g[2]) >= similarity:
                g[1] = day
                g[3].append(p)
                break
        else:
            groups.append([day, day, words, [p]])
    return [g[3] for g in groups]


def top_posts(posts, n=TOP_STORIES, similarity=DEFAULTS["story_similarity"]):
    """The top n stories. With engagement on the posts: the n posts with the highest engagement.
    Otherwise: stories ranked by distinct sources, then headlines, then the earliest date; each with its
    first headline and link. Returns (ranking, stories)."""
    if any(p.engagement is not None for p in posts):
        chosen = sorted(posts, key=lambda p: (-(p.engagement or 0), p.date, p.url))[:n]
        return "engagement", [{**_story_entry(p, 1, 1), "engagement": p.engagement or 0} for p in chosen]
    groups = group_stories(posts, similarity)
    ranked = sorted(groups, key=lambda g: (-len({p.source for p in g}), -len(g), g[0].date, g[0].url))
    return "distinct sources", [_story_entry(g[0], len({p.source for p in g}), len(g)) for g in ranked[:n]]


def share(counts, key="negative"):
    return percentages(counts)[key] if sum(counts.values()) else None


def trend_report(posts, keyword, start, end, today, previous=None, previous_note=None, coverage=None,
                 sentiment_labels=None, kind="news", unit="auto", settings=None, max_tokens=MAX_TOKENS):
    """The report for posts already filtered by keyword and range. previous: (start, end, posts) of the
    period just before, or None with previous_note saying why. Pure: no I/O, no clock."""
    cfg = {**DEFAULTS, **(settings or {})}
    start, end, today = parse_day(start, "start"), parse_day(end, "end"), parse_day(today, "today")
    unit = choose_unit(start, end, unit)
    labels = slots(start, end, unit)
    index = {label: i for i, label in enumerate(labels)}
    series = {k: [0] * len(labels) for k in ("total", "negative", "positive", "neutral")}
    for p in posts:
        i = index.get(slot_of(p, unit))
        if i is None:
            continue
        series["total"][i] += 1
        if p.sentiment in series:
            series[p.sentiment][i] += 1
    counts = Counter(p.sentiment or "unlabelled" for p in posts)
    total = len(posts)
    peak_i = max(range(len(labels)), key=lambda i: (series["total"][i], -i)) if labels else None
    out = {
        "keyword": keyword, "source": kind, "start_date": start.isoformat(), "end_date": end.isoformat(),
        "today": today.isoformat(), "today_weekday": WEEKDAYS_ID[today.weekday()],
        "relative_dates": relative_dates(today),
        "total": total,
        "sentiment_total": {"counts": {k: counts.get(k, 0) for k in SENTIMENTS}, "pct": percentages(counts)},
        "peak": {"at": labels[peak_i], "count": series["total"][peak_i]} if total else None,
    }
    if previous:
        p_start, p_end, p_posts = previous
        p_counts = Counter(p.sentiment or "unlabelled" for p in p_posts)
        now_neg, before_neg = share(counts), share(p_counts)
        out["change"] = {
            "previous_start": p_start.isoformat(), "previous_end": p_end.isoformat(),
            "previous_total": len(p_posts), "total_diff": total - len(p_posts),
            "pct_change": round((total - len(p_posts)) * 100 / len(p_posts), 1) if p_posts else None,
            "previous_negative_share": before_neg,
            "negative_share_diff": round(now_neg - before_neg, 1) if total and p_posts else None,
        }
    else:
        out["change"] = None
    ranking, stories = top_posts(posts, TOP_STORIES, cfg["story_similarity"])
    out["top_stories_ranked_by"] = ranking
    out["top_stories"] = stories
    out["trend"] = {"unit": unit, "labels": labels, **series}
    out["data_warnings"] = warnings(total, previous, previous_note, counts, sentiment_labels, kind,
                                    ranking, bool(stories))
    out = {"must_tell_user": must_tell_user(out["data_warnings"]), **out}
    return fit(out, max_tokens) if max_tokens is not None else out


def warnings(total, previous, previous_note, counts, sentiment_labels, kind, ranking, stories_shown):
    out = []
    if not total:
        out.append("no posts found for this keyword and date range")
    if previous_note:
        out.append(previous_note)
    elif previous is not None:
        before = len(previous[2])
        if not before:
            out.append("the previous period has 0 posts, so the percent change is not given (null)")
        elif min(total, before) < SMALL_COUNT:
            out.append(f"small counts ({total} now, {before} before; fewer than {SMALL_COUNT}) make the "
                       "change unreliable")
    elif total and total < SMALL_COUNT:
        out.append(f"only {total} posts (fewer than {SMALL_COUNT}); treat the trend as a rough hint")
    labelled = total - counts.get("unlabelled", 0)
    if labelled and sentiment_labels == "model":
        out.append("sentiment labels are rough model output (63% agreement with a person, kappa 0.44); "
                   "treat the shares as an estimate")
    if stories_shown and ranking == "distinct sources":
        out.append("top stories group similar headlines automatically; the grouping is approximate")
    if kind == "news":
        out.append("source is news headlines only (no article text, no social media)")
    return out


def fit(out, max_tokens=MAX_TOKENS):
    """Keep the output under max_tokens: shorter story texts, fewer stories, then no per-point sentiment,
    then no per-point table (with a warning). Totals, sentiment_total, peak and change are never cut."""
    for chars in (80, 50):
        if estimate_tokens(out) <= max_tokens:
            break
        for s in out.get("top_stories", []):
            if len(s["text"]) > chars:
                s["text"] = s["text"][:chars - 1].rstrip() + "…"
    while estimate_tokens(out) > max_tokens and len(out.get("top_stories", [])) > 3:
        out["top_stories"].pop()
    trend = out.get("trend", {})
    if estimate_tokens(out) > max_tokens and "positive" in trend:
        for k in ("positive", "neutral"):
            trend.pop(k)
    if estimate_tokens(out) > max_tokens and "total" in trend:
        n, u = len(trend["labels"]), trend["unit"]
        out["trend"] = {"unit": u, "removed": True}
        out["data_warnings"].append(f"the {u}-by-{u} table ({n} points) was left out to keep the result "
                                    "short; ask for a shorter period to see it")
        out["must_tell_user"] = must_tell_user(out["data_warnings"])
    out["output_tokens_estimate"] = 0
    out["output_tokens_estimate"] = estimate_tokens(out)
    return out


def entity_trend(adapter, keyword, start_date=None, end_date=None, today=None, sentiment_labels=None,
                 period=None, source="news", unit="auto", settings=None, *, max_tokens=MAX_TOKENS):
    """Trend of one keyword over start_date..end_date (WIB, inclusive) or a period word, with the change
    against the prior period and the top stories. Pass max_tokens=None for full data outside chat."""
    today = parse_day(today, "today") if today else datetime.now(WIB).date()
    kind = "social" if source == "social" else "news"
    pick = (lambda p: p.platform == "news") if kind == "news" else (lambda p: p.platform != "news")
    keyword = " ".join(str(keyword).split())
    try:
        start_date, end_date, word = resolve_period(period, start_date, end_date, today)
        start, end = parse_day(start_date, "start_date"), parse_day(end_date, "end_date")
        if start > end:
            raise QueryError(f"start_date {start} is after end_date {end}; swap them")
        days = (end - start).days + 1
        p_start, p_end = start - timedelta(days=days), start - timedelta(days=1)
        previous_note = None
        if hasattr(adapter, "posts"):
            coverage = data_range(adapter.posts)
            if not any(pick(p) for p in adapter.posts):
                return no_data(keyword, start, end, today, None, word, kind)
            if start < coverage[0] or end > coverage[1]:
                return no_data(keyword, start, end, today, coverage, word, kind)
            if p_start < coverage[0]:
                previous_note = (f"the previous period ({human_range(p_start, p_end)}) is not fully inside "
                                 f"the data, which starts on {coverage[0]}, so no change numbers are given")
        else:
            coverage = None
        found = [p for p in adapter.search(keyword, start, end, today=today) if pick(p)]
        previous = None
        if not previous_note:
            previous = (p_start, p_end, [p for p in adapter.search(keyword, p_start, p_end, today=today)
                                         if pick(p)])
    except QueryError as e:
        return {"error": str(e), "today": today.isoformat(), "today_weekday": WEEKDAYS_ID[today.weekday()]}
    out = trend_report(found, keyword, start, end, today, previous, previous_note, coverage, sentiment_labels,
                       kind, unit, settings, max_tokens=max_tokens)
    out = {"must_tell_user": out.pop("must_tell_user"), "data_available": True, **out}
    if word:
        out["period"] = word
    return fit(out, max_tokens) if max_tokens is not None else out


def from_config(config):
    """Keyword arguments for entity_trend taken from a Config."""
    return {"today": config.today, "sentiment_labels": config.data_source.sentiment_labels,
            "settings": {"story_similarity": config.trend.story_similarity}}


__all__ = ["entity_trend", "trend_report", "top_posts", "group_stories", "normalise", "jaccard",
           "from_config"]
