"""Read news posts from RSS 2.0 or Atom feeds, with the standard library only.

The adapter takes feed content that is already downloaded (bytes or str) or a local file path.
fetch_feed() is a separate, optional helper for downloading a feed.

XML safety: feeds that contain a DOCTYPE or ENTITY declaration are refused before parsing, so no
external entities are resolved and entity-expansion attacks can't run.
"""
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

from slmcp import __version__
from slmcp.adapters.base import Adapter, filter_posts
from slmcp.schema import PostError, from_dict

ATOM = "{http://www.w3.org/2005/Atom}"
USER_AGENT = f"social-listening-mcp/{__version__} (feed reader)"
MAX_FEED_BYTES = 5_000_000


class FeedError(ValueError):
    """A feed that can't be read at all (not XML, unsafe, or not RSS/Atom)."""


BLOCK_TAGS = {"p", "br", "div", "li", "ul", "ol", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
              "blockquote", "figcaption", "img"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        self.handle_starttag(tag, ())

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


def strip_html(value):
    """Plain text from an HTML fragment: tags removed, entities decoded, spaces collapsed."""
    parser = _Text()
    parser.feed(value or "")
    parser.close()
    return " ".join("".join(parser.parts).split())


def parse_date(value):
    """RFC 822 (RSS pubDate) or ISO 8601 (Atom) -> aware datetime, or None if unreadable."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            d = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
    # "-0000" in RFC 822 and dates without an offset are read as UTC
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _parse(content):
    if isinstance(content, bytes):
        head = content[:4096].decode("utf-8", "replace")
    else:
        head = content[:4096]
    if re.search(r"<!DOCTYPE|<!ENTITY", head, re.IGNORECASE):
        raise FeedError("feed has a DOCTYPE or ENTITY declaration; refused for safety")
    try:
        return ET.fromstring(content)
    except ET.ParseError as e:
        raise FeedError(f"not valid XML ({e})") from None


def _txt(el, path):
    found = el.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def _atom_link(entry):
    for link in entry.findall(f"{ATOM}link"):
        if link.get("rel", "alternate") == "alternate" and link.get("href"):
            return link.get("href").strip()
    return ""


def read_feed(content):
    """Turn one feed into (rows, feed_title). Each row is a dict for from_dict (may still be invalid)."""
    root = _parse(content)
    rows = []
    if root.tag == "rss" or root.find("channel") is not None:
        channel = root.find("channel")
        if channel is None:
            raise FeedError("RSS feed without a <channel>")
        source = _txt(channel, "title")
        for item in channel.findall("item"):
            rows.append(_row(_txt(item, "title"), _txt(item, "description"), _txt(item, "link"),
                             _txt(item, "pubDate"), source))
    elif root.tag == f"{ATOM}feed":
        source = _txt(root, f"{ATOM}title")
        for entry in root.findall(f"{ATOM}entry"):
            summary = _txt(entry, f"{ATOM}summary") or _txt(entry, f"{ATOM}content")
            when = _txt(entry, f"{ATOM}published") or _txt(entry, f"{ATOM}updated")
            rows.append(_row(_txt(entry, f"{ATOM}title"), summary, _atom_link(entry), when, source))
    else:
        raise FeedError(f"not an RSS or Atom feed (root element {root.tag!r})")
    return rows, source


def _row(title, description, link, when, source):
    title, description = strip_html(title), strip_html(description)
    text = f"{title}. {description}" if title and description else title or description
    date = parse_date(when)
    return {"date": date.isoformat() if date else when, "text": text, "source": source,
            "platform": "news", "url": link}


class RssAdapter(Adapter):
    """Posts from one or more feeds. feeds: items that are bytes/str feed content or a Path to a file.
    Items that don't fit the schema (no link, no date, no title) are skipped; see skipped and errors."""

    def __init__(self, feeds, max_days=None):
        if isinstance(feeds, (bytes, str, Path)):
            feeds = [feeds]
        if max_days is not None:
            self.max_days = max_days
        self.posts, self.errors = [], []
        for n, feed in enumerate(feeds, start=1):
            content = feed.read_bytes() if isinstance(feed, Path) else feed
            rows, source = read_feed(content)
            for i, row in enumerate(rows, start=1):
                try:
                    self.posts.append(from_dict(row))
                except PostError as e:
                    self.errors.append(f"feed {n} ({source or 'untitled'}) item {i}: {e}")

    @property
    def skipped(self):
        return len(self.errors)

    def first_errors(self, n=5):
        return self.errors[:n]

    def _search(self, keyword, start, end):
        return filter_posts(self.posts, keyword, start, end)


def fetch_feed(url, timeout=10):
    """Download one feed (optional helper; tests never call it). Returns bytes, at most 5 MB."""
    if not url.startswith(("https://", "http://")):
        raise FeedError(f"{url!r}: only http(s) feeds can be fetched")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(MAX_FEED_BYTES + 1)
    if len(data) > MAX_FEED_BYTES:
        raise FeedError(f"{url}: feed is larger than {MAX_FEED_BYTES} bytes")
    return data
