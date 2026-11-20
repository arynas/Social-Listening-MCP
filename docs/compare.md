# compare_keywords

Puts 2 or 3 keywords side by side for the same period. Everything is computed by code (`slmcp/compare.py`); the model only reports it.

## Input
- `keywords` (required): a list of 2 or 3 keywords, each 1 or 2 words (`["banjir", "gempa"]`). One keyword, 4 or more, or the same keyword twice is refused with a short error (for one keyword, use `news_recap`). Each keyword gets the same length check as the other tools.
- `period`, `start_date`, `end_date`: as in `news_recap` (`docs/tools.md`), including the strict data range: a period not fully inside the data gets `data_available: false` and no numbers.
- `source`: `news` (default) or `social`.

## Method
- **Keyword match:** the same shared matcher as `news_recap` / `social_recap` (substring, case-insensitive, `filter_posts`). So each keyword's `total` and `sentiment_total` are exactly what `news_recap` gives for that keyword and period (a test checks this). `detect_spikes` matches whole words instead (`ott` does not match "Baggott"), because a few wrong matches can create a false spike. This tool keeps the recap rule so that its numbers agree with recap answers in the same conversation; the price is that `banjir` also counts "kebanjiran".
- **Trend:** counts per WIB day, or per WIB hour when the range has 3 days or fewer (the same rule as `detect_spikes`).
- **Pairs:** for each pair (a, b): `total_diff` (a − b), `total_ratio` (a / b), `negative_share_diff` (percentage points), `negative_share_ratio`, and on how many days (or hours) a is higher, b is higher, or they are tied. A ratio that would divide by 0 is `null`; share differences are `null` when either keyword has 0 headlines.
- **Overlap:** a headline that matches 2 or more keywords is counted in each keyword's total. `overlap` gives how many headlines that is, the number of distinct headlines, and the count per pair (and in all three, for 3 keywords).

## Output
- `must_tell_user` first, then `data_available`, `keywords`, `source`, the period, `method`.
- `per_keyword`: `keyword`, `total`, `sentiment_total` (counts and percent, adding up to 100.0), `negative_share`, up to 3 `samples` (headline + link from the data).
- `pairs`, `overlap`, `trend` (`unit`, `labels`, `counts` per keyword).
- `data_warnings`: a keyword with 0 headlines (shown as 0, no ratios that divide by it), fewer than 10 headlines (differences and ratios unreliable), overlap present, rough model sentiment labels (kappa 0.44), news headlines only.
- The result stays under about 2,000 tokens: shorter sample texts, then fewer samples, then the trend table is left out (with a warning that says so). Totals, sentiment, pairs and overlap are never cut.
