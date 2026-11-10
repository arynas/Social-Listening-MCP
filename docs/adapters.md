# Adapters

An adapter connects one data source (a CSV file, an RSS feed, a database) to the shared post schema (`docs/schema.md`). Tools only call `adapter.search(keyword, start, end)` and get back a list of `Post`.

## The contract
- `keyword`: a case-insensitive **phrase** match on the post `text`. `jalur sepeda` matches "Jalur Sepeda baru" but not "sepeda di jalur". Extra spaces are ignored. Empty keywords are refused.
- `start`, `end`: days as `YYYY-MM-DD` (or `date`). Both are **inclusive**, and a post's day is its day in **WIB** (Asia/Jakarta): a post at 23:30 WIB on the end day is included, one at 00:10 WIB the next day is not.
- Refused with a clear `QueryError`: an empty keyword, `start` after `end`, a range over **366 days** (set `max_days` on the adapter to change it), an `end` after today (WIB), a date that isn't `YYYY-MM-DD`.
- The result is sorted by date (oldest first); posts with the same time keep the source's order. No match gives an empty list, not an error.

Two helpers in `slmcp/adapters/base.py` make this easy:
- `filter_posts(posts, keyword, start, end)`: applies the whole rule to a list of `Post`. Simple adapters (files, feeds) just call it.
- `utc_window(start, end)`: the WIB days as a UTC window `[from, to)`, for adapters that query a database.

The base class `Adapter` (in `slmcp/adapters/base.py`) checks the request and sorts the result, so an adapter only has to find the matching posts.

## Write a new adapter in 5 steps
1. Create `slmcp/adapters/<name>.py` with a class that inherits `Adapter`.
2. In `__init__`, take what the source needs (a file path, a feed URL). Read secrets only from environment variables.
3. Turn each source row into a `Post` with `slmcp.schema.from_dict` (use `assume_tz=WIB` if the source writes local times without a time zone). Skip and count rows that fail.
4. Implement `_search(keyword, start, end)`: return `filter_posts(all_posts, keyword, start, end)`, or run the same filter in the source's own query language using `utc_window`.
5. Add `tests/test_<name>.py` with a class that inherits `AdapterContract` (from `tests/adapter_contract.py`) and implements `make(posts)`: load exactly the given posts into your source (e.g. write them to a temp file in your format). If the contract tests pass, the adapter works with every tool.

See `slmcp/adapters/memory.py` for the smallest example.
