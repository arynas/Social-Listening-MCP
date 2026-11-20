"""Load and check the YAML config, and build the adapter it names.

Keys and tokens never go in the file: the model section names an environment variable (api_key_env)
and the key is read from there. A config with a key-like field that has a value is refused.
"""
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

ADAPTERS = ("csv", "jsonl", "rss")
SECRET_WORDS = ("key", "token", "password", "secret")


class ConfigError(ValueError):
    """A config file that can't be used. The message names the field and how to fix it."""


@dataclass(frozen=True)
class DataSource:
    adapter: str
    paths: tuple = ()
    urls: tuple = ()
    columns: dict = field(default_factory=dict)
    defaults: dict = field(default_factory=dict)
    assume_tz: str | None = None
    sentiment_labels: str | None = None  # "model" or "human": who labelled the sentiment, if anyone


@dataclass(frozen=True)
class Model:
    endpoint: str
    name: str
    api_key_env: str | None = None

    def api_key(self):
        """The key from the named environment variable, or None if no variable is named."""
        if not self.api_key_env:
            return None
        value = os.environ.get(self.api_key_env)
        if not value:
            raise ConfigError(f"model.api_key_env: environment variable {self.api_key_env} is not set")
        return value


@dataclass(frozen=True)
class Spikes:
    """Settings for detect_spikes (see docs/spikes.md)."""
    window_days: int = 7       # trailing baseline window for daily counts
    window_hours: int = 24     # trailing baseline window for hourly counts
    z_threshold: float = 3.0   # robust z score at or above which a point is a spike
    min_count: int = 3         # a spike needs at least this many posts
    min_ratio: float = 2.0     # and at least this many times the baseline median (passes if the median is 0)
    min_count_zero_median: int = 10  # when the baseline median is 0, a spike needs at least this many posts


@dataclass(frozen=True)
class Trend:
    """Settings for entity_trend (see docs/trend.md)."""
    story_similarity: float = 0.5  # word overlap (Jaccard) at or above which two headlines are one story


@dataclass(frozen=True)
class Config:
    data_source: DataSource
    model: Model
    language: str = "id"
    timezone: str = "Asia/Jakarta"
    today: str | None = None  # frozen "today" (YYYY-MM-DD) for a fixed snapshot; None = the real date
    spikes: Spikes = field(default_factory=Spikes)
    trend: Trend = field(default_factory=Trend)


def _find_secrets(node, where=""):
    if isinstance(node, dict):
        for k, v in node.items():
            name = f"{where}.{k}" if where else str(k)
            low = str(k).lower()
            if any(w in low for w in SECRET_WORDS) and not low.endswith("_env") and v not in (None, ""):
                yield name
            yield from _find_secrets(v, name)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _find_secrets(v, f"{where}[{i}]")


def _as_list(value, name):
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    raise ConfigError(f"{name}: give one string or a list of strings")


def _zone(value, name):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ConfigError(f"{name}: {value!r} is not a time zone name, e.g. Asia/Jakarta") from None
    return value


def _spikes(raw):
    if raw is None:
        return Spikes()
    if not isinstance(raw, dict):
        raise ConfigError("spikes: give a mapping, e.g. {window_days: 7, z_threshold: 3}")
    unknown = set(raw) - {"window_days", "window_hours", "z_threshold", "min_count", "min_ratio",
                          "min_count_zero_median"}
    if unknown:
        raise ConfigError(f"spikes: unknown field(s) {', '.join(sorted(unknown))}")
    out = {}
    for name, kind, low in (("window_days", int, 3), ("window_hours", int, 3), ("z_threshold", float, 1),
                            ("min_count", int, 1), ("min_ratio", float, 1),
                            ("min_count_zero_median", int, 3)):
        if name in raw:
            value = raw[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < low or \
                    (kind is int and value != int(value)):
                raise ConfigError(f"spikes.{name}: {value!r} must be a number of at least {low}"
                                  + (" (whole number)" if kind is int else ""))
            out[name] = kind(value)
    spikes = Spikes(**out)
    if spikes.min_count_zero_median < spikes.min_count:
        raise ConfigError(f"spikes.min_count_zero_median: {spikes.min_count_zero_median} must be at least "
                          f"min_count ({spikes.min_count})")
    return spikes


def _trend(raw):
    if raw is None:
        return Trend()
    if not isinstance(raw, dict):
        raise ConfigError("trend: give a mapping, e.g. {story_similarity: 0.6}")
    unknown = set(raw) - {"story_similarity"}
    if unknown:
        raise ConfigError(f"trend: unknown field(s) {', '.join(sorted(unknown))}")
    value = raw.get("story_similarity", Trend.story_similarity)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 1:
        raise ConfigError(f"trend.story_similarity: {value!r} must be a number above 0 and at most 1")
    return Trend(story_similarity=float(value))


def parse_config(raw, base_dir="."):
    """A dict (from YAML) -> Config, or ConfigError. Relative paths are resolved against base_dir."""
    if not isinstance(raw, dict):
        raise ConfigError("config: the file must be a mapping with data_source and model")
    secrets = list(_find_secrets(raw))
    if secrets:
        raise ConfigError(f"{', '.join(secrets)}: don't put keys or tokens in the config; set "
                          "model.api_key_env to the name of an environment variable instead")
    ds, model = raw.get("data_source"), raw.get("model")
    if not isinstance(ds, dict):
        raise ConfigError("data_source: missing; give at least adapter and path")
    if not isinstance(model, dict):
        raise ConfigError("model: missing; give endpoint and name")
    adapter = str(ds.get("adapter", "")).lower()
    if adapter not in ADAPTERS:
        raise ConfigError(f"data_source.adapter: {ds.get('adapter')!r} is not one of {', '.join(ADAPTERS)}")
    base = Path(base_dir)
    paths = tuple(str(p if Path(p).is_absolute() else base / p)
                  for p in _as_list(ds.get("path", ds.get("paths")), "data_source.path"))
    urls = _as_list(ds.get("url", ds.get("urls")), "data_source.url")
    if adapter in ("csv", "jsonl") and not paths:
        raise ConfigError(f"data_source.path: a {adapter} source needs at least one file path "
                          "(several files, e.g. one per month, are read as one source)")
    if adapter == "rss" and not (paths or urls):
        raise ConfigError("data_source: an rss source needs path (saved feeds) or url (live feeds)")
    for p in paths:
        if not Path(p).exists():
            raise ConfigError(f"data_source.path: {p} does not exist")
    for name in ("columns", "defaults"):
        if not isinstance(ds.get(name) or {}, dict):
            raise ConfigError(f"data_source.{name}: give a mapping, e.g. {{created_at: date}}")
    assume_tz = ds.get("assume_tz")
    if assume_tz is not None:
        _zone(assume_tz, "data_source.assume_tz")
    labels = ds.get("sentiment_labels")
    if labels not in (None, "model", "human"):
        raise ConfigError(f"data_source.sentiment_labels: {labels!r} must be model, human or empty")
    today = raw.get("today")
    if today is not None:
        try:
            today = date.fromisoformat(str(today)).isoformat()
        except ValueError:
            raise ConfigError(f"today: {today!r} is not a date; use YYYY-MM-DD, e.g. 2026-10-15") from None
    endpoint, name = model.get("endpoint"), model.get("name")
    if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
        raise ConfigError("model.endpoint: give an http(s) URL, e.g. http://localhost:8000/v1")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError("model.name: missing")
    return Config(
        data_source=DataSource(adapter=adapter, paths=paths, urls=urls, columns=dict(ds.get("columns") or {}),
                               defaults=dict(ds.get("defaults") or {}), assume_tz=assume_tz,
                               sentiment_labels=labels),
        model=Model(endpoint=endpoint, name=name.strip(), api_key_env=model.get("api_key_env") or None),
        language=str(raw.get("language", "id")),
        timezone=_zone(raw.get("timezone", "Asia/Jakarta"), "timezone"),
        today=today,
        spikes=_spikes(raw.get("spikes")),
        trend=_trend(raw.get("trend")))


def load_config(path):
    """Read a YAML config file (safe_load only) and check it."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"{path.name}: not valid YAML ({e})") from None
    return parse_config(raw, base_dir=path.parent)


def build_adapter(config):
    """The adapter that the config's data_source names."""
    ds = config.data_source
    if ds.adapter in ("csv", "jsonl"):
        from slmcp.adapters.csv_json import FileAdapter
        files = [FileAdapter(p, columns=ds.columns, defaults=ds.defaults,
                             assume_tz=ZoneInfo(ds.assume_tz) if ds.assume_tz else None) for p in ds.paths]
        if len(files) == 1:
            return files[0]
        from slmcp.adapters.memory import MemoryAdapter  # several files (e.g. one per month) as one source
        return MemoryAdapter([p for f in files for p in f.posts])
    from slmcp.adapters.rss import RssAdapter, fetch_feed
    feeds = [Path(p) for p in ds.paths] + [fetch_feed(u) for u in ds.urls]
    return RssAdapter(feeds)
