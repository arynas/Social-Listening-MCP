"""The shared post schema. Every adapter turns its source into a list of Post."""
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

PLATFORMS = ("news", "twitter", "instagram", "facebook", "youtube", "tiktok", "other")
SENTIMENTS = ("positive", "neutral", "negative")
WIB = ZoneInfo("Asia/Jakarta")  # UTC+7, no daylight saving
REQUIRED = ("date", "text", "source", "platform", "url")


class PostError(ValueError):
    """A row that does not fit the schema. The message says which field and how to fix it."""


@dataclass(frozen=True)
class Post:
    date: datetime  # timezone-aware, stored in UTC
    text: str
    source: str  # media name or account
    platform: str
    url: str
    author: str | None = None
    engagement: int | None = None
    sentiment: str | None = None

    @property
    def date_wib(self):
        """The date in Asia/Jakarta time (WIB)."""
        return self.date.astimezone(WIB)

    def to_dict(self):
        return {"date": self.date.isoformat(), "text": self.text, "source": self.source,
                "platform": self.platform, "url": self.url, "author": self.author,
                "engagement": self.engagement, "sentiment": self.sentiment}


def _date(value, assume_tz):
    if isinstance(value, datetime):
        d = value
    else:
        try:
            d = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            raise PostError(f"date: {value!r} is not ISO 8601, e.g. 2026-11-09T14:30:00+07:00") from None
    if d.tzinfo is None:
        if assume_tz is None:
            raise PostError(f"date: {value!r} has no time zone; add one, e.g. +07:00 for WIB")
        d = d.replace(tzinfo=assume_tz)
    return d.astimezone(UTC)


def _text(row, name):
    value = row.get(name)
    if value is None or not str(value).strip():
        raise PostError(f"{name}: missing or empty")
    return str(value).strip()


def _optional_text(value):
    return str(value).strip() or None if value is not None else None


def from_dict(row, assume_tz=None):
    """Build a Post from a dict, or raise PostError. assume_tz is used for dates without a time zone
    (e.g. WIB for a source that writes local times); by default such dates are refused."""
    missing = [f for f in REQUIRED if row.get(f) in (None, "")]
    if missing:
        raise PostError(f"missing required field(s): {', '.join(missing)}")
    platform = _text(row, "platform").lower()
    if platform not in PLATFORMS:
        raise PostError(f"platform: {platform!r} is not one of {', '.join(PLATFORMS)}")
    url = _text(row, "url")
    if not url.startswith(("http://", "https://")):
        raise PostError(f"url: {url!r} must start with http:// or https://")
    sentiment = _optional_text(row.get("sentiment"))
    if sentiment is not None:
        sentiment = sentiment.lower()
        if sentiment not in SENTIMENTS:
            raise PostError(f"sentiment: {sentiment!r} is not one of {', '.join(SENTIMENTS)} (or empty)")
    engagement = row.get("engagement")
    if engagement in (None, ""):
        engagement = None
    else:
        try:
            engagement = int(engagement)
        except (TypeError, ValueError):
            raise PostError(f"engagement: {engagement!r} is not a whole number") from None
        if engagement < 0:
            raise PostError(f"engagement: {engagement} must be 0 or more")
    return Post(date=_date(row["date"], assume_tz), text=_text(row, "text"), source=_text(row, "source"),
                platform=platform, url=url, author=_optional_text(row.get("author")),
                engagement=engagement, sentiment=sentiment)
