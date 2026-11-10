# Post schema

Every data source is turned into a list of `Post` (see `slmcp/schema.py`). Tools only read `Post`, never a source directly.

| Field | Required | Type and rule | Example |
|---|---|---|---|
| `date` | yes | ISO 8601 with a time zone. Stored in UTC; `date_wib` gives the time in Asia/Jakarta (WIB, UTC+7). A date without a time zone is refused, unless the adapter passes `assume_tz` (e.g. `WIB` for a source that writes local times). | `2026-11-09T08:15:00+07:00` |
| `text` | yes | Non-empty text of the post or the article title/summary. | `Pemerintah kota meresmikan…` |
| `source` | yes | Media name or account that published it. | `Kabar Kota Contoh`, `@warga_contoh` |
| `platform` | yes | One of `news`, `twitter`, `instagram`, `facebook`, `youtube`, `tiktok`, `other` (case-insensitive). | `news` |
| `url` | yes | Link to the original, starting with `http://` or `https://`. Tools only show links that come from here. | `https://example.com/berita/…` |
| `author` | no | Person or account name, if different from `source`. | `warga_contoh` |
| `engagement` | no | Whole number, 0 or more (likes + shares + comments, or views, as the adapter defines). | `37` |
| `sentiment` | no | `positive`, `neutral` or `negative` (case-insensitive), or empty. | `negative` |

## Building a post

```python
from slmcp.schema import from_dict, PostError

post = from_dict({"date": "2026-11-09T08:15:00+07:00", "text": "…", "source": "Media Contoh",
                  "platform": "news", "url": "https://example.com/a"})
```

A row that does not fit raises `PostError`. The message names the field and says how to fix it, for example `date: '2026-11-09T08:15:00' has no time zone; add one, e.g. +07:00 for WIB`.

## Examples

`examples/posts_sample.jsonl` has 3 synthetic rows (news, twitter, youtube). All names and links are made up (`example.com`).
