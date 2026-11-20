# entity_trend (with top stories)

The trend of one person, place or topic over a period, compared with the period just before, plus the top stories. Everything is computed by code (`slmcp/trend.py`); the model only reports it. `top_posts` is a pure function inside the same module; it is not a separate tool, so the tool list stays at five tools.

## Input
- `keyword` (required): 1 or 2 words (`prabowo`, `harga beras`), with the same length check as the other tools.
- `period`, `start_date`, `end_date`: as in `news_recap` (`docs/tools.md`), including the strict data range.
- `source`: `news` (default) or `social`.

## Method
- **Keyword match:** the same shared matcher as `news_recap` (substring, case-insensitive), so `total` and `sentiment_total` equal what `news_recap` gives (a test checks this).
- **Trend:** posts and sentiment counts (negative, positive, neutral) per WIB day, or per WIB hour when the range has 3 days or fewer. `peak` is the day (or hour) with the most posts; on a tie, the earliest.
- **Change:** against the previous period of the same length, the days just before (for 1–14 Oct: 17–30 Sep). `total_diff`, `pct_change` (null when the previous period has 0 posts), `previous_negative_share` and `negative_share_diff` (percentage points; null when either period has 0 posts). If the previous period is not fully inside the data, `change` is null and a warning says so: no data, no numbers.
- **Top stories (`top_posts`):**
  - News: headlines are normalised (lowercase, no punctuation, a trailing site name of at most 3 words after " - ", " | ", " : ", " – " is removed, e.g. " : Okezone News", " - Kompas").
  - A headline joins a story when it is on the same day as the story's last headline or the day after, and its word overlap (Jaccard) with the story's first headline is at least `story_similarity` (default 0.5, config `trend:`). On the S1 snapshot 0.6 split rewritten headlines of one event into several stories, and 0.3 merged far too much; 0.5 groups clear rewrites ("KPK OTT di BNPB" and "KPK Lakukan OTT di BNPB, Sejumlah Pihak Diamankan", overlap 0.5).
  - Stories are ranked by the number of distinct sources, then headlines, then the earliest date. Each story shows its first headline with link, date, source, `sources` and `headlines`.
  - The grouping is approximate: rewritten headlines of the same event usually stay apart, so the counts are a lower bound.
  - Posts with an engagement field (social data, tested with synthetic data) are ranked by engagement instead, one post per entry.

```yaml
trend:                   # optional; this is the default
  story_similarity: 0.5  # word overlap at or above which two headlines are the same story
```

## Output
- `must_tell_user` first, then `data_available`, `keyword`, `source`, the period, `total`, `sentiment_total`, `peak`, `change`, `top_stories_ranked_by`, `top_stories` (5), `trend` (`unit`, `labels`, `total`, `negative`, `positive`, `neutral`).
- `data_warnings`: no posts, previous period not inside the data, small counts (fewer than 10), rough model sentiment labels (kappa 0.44), approximate story grouping, news headlines only.
- The result stays under about 2,000 tokens: shorter story texts, then 3 stories, then no positive/neutral per point, then no table (with a warning).
