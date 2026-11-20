# Spike detection (`detect_spikes`)

`detect_spikes` finds days (or hours) with unusually many posts about a keyword, compared with the days just before. Numbers are computed by code; the model only reports them.

## Method
- **Unit:** posts are counted per WIB day, or per WIB hour when the asked range has 3 days or fewer.
- **Baseline:** for each point, the median of the previous `window` points (trailing). Days before the asked range are used when they are inside the data, so the first asked day can be scored too.
- **Spread:** 1.4826 × MAD (median absolute deviation) of those baseline points. If the MAD is 0, the spread is √median, at least 1.
- **Score:** robust z = (count − median) / spread. A point is a **spike** when z ≥ `z_threshold` and count ≥ `min_count`.
- A point needs at least 3 earlier points to be scored; points without them are counted in `unscored_points`.
- **Negative spikes:** the same rule on the number of negative posts, reported separately in `negative_spikes` (only for labelled data).

Worked example (also a test): baseline `[4, 5, 6, 5, 4, 5, 6]` → median 5, MAD 1, spread 1.4826. A day with 20 posts has z = 15 / 1.4826 = 10.1 and ratio 20 / 5 = 4.0, so it is a spike.

## Settings (config)
```yaml
spikes:              # optional; these are the defaults
  window_days: 7     # trailing baseline for daily counts
  window_hours: 24   # trailing baseline for hourly counts
  z_threshold: 3     # robust z at or above which a point is a spike
  min_count: 3       # a spike needs at least this many posts
```

## Output
- `must_tell_user` first, then `data_available`, the period, `unit`, `points`, `total`, `method`, `unscored_points`, `spike_count`.
- `spikes`: at most 10, highest z first. Each has `at` (day or hour), `count`, `baseline_median`, `spread`, `z`, `ratio_to_median`, `negative` (if labelled), `top_sources` (3) and up to 3 `samples` (headline + link from the data).
- `negative_spikes` and `negative_spike_count` for labelled data; `counts` per point when there are 72 points or fewer.
- `data_warnings`: no posts, a short baseline (fewer than 7 points for the earliest scored point), rough model sentiment labels when negative spikes are shown, headlines only, and "a spike is a statistical signal, not proof of an event".
- The result stays under about 2,000 tokens (fewer samples, then no `counts`, then fewer spikes).

Period words, the strict data range (no data, no numbers), keyword checks and the one-line JSON log work exactly as in `news_recap` (`docs/tools.md`).

## Limits
- A spike is relative to the previous days: a slow rise is not a spike, and the day after a big spike can look normal.
- With a very flat baseline (small spread), a modest rise can score high; check `ratio_to_median` too.
- The public snapshot `data-2026-10-v1` has 14 days, so a 7-day baseline leaves few scored days; hourly counts help for short ranges.
