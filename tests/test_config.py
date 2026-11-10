import json
from datetime import date
from pathlib import Path

import pytest

from slmcp.adapters.csv_json import FileAdapter
from slmcp.adapters.rss import RssAdapter
from slmcp.config import ConfigError, build_adapter, load_config, parse_config

ROOT = Path(__file__).parent.parent
MODEL = {"endpoint": "http://localhost:8000/v1", "name": "Qwen3.6-27B"}


def write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_example_config_loads_and_builds_the_csv_adapter():
    config = load_config(ROOT / "config.example.yaml")
    assert config.model.name == "Qwen3.6-27B" and config.model.api_key() is None
    adapter = build_adapter(config)
    assert isinstance(adapter, FileAdapter) and adapter.skipped == 0 and len(adapter.posts) == 80


def test_switching_adapter_needs_only_a_config_change(tmp_path):
    feed = ROOT / "tests" / "fixtures" / "feed_rss.xml"
    jsonl = tmp_path / "posts.jsonl"
    jsonl.write_text(json.dumps({"date": "2026-11-09T08:00:00+07:00", "text": "Jalur sepeda baru",
                                 "source": "Media Contoh", "platform": "news",
                                 "url": "https://example.com/1"}) + "\n")
    rss = build_adapter(load_config(write(tmp_path, f"data_source: {{adapter: rss, path: {feed}}}\n"
                                                    "model: {endpoint: 'http://x/v1', name: m}\n")))
    js = build_adapter(load_config(write(tmp_path, "data_source: {adapter: jsonl, path: posts.jsonl}\n"
                                                   "model: {endpoint: 'http://x/v1', name: m}\n")))
    assert isinstance(rss, RssAdapter) and isinstance(js, FileAdapter)
    today = date(2026, 11, 10)
    assert len(rss.search("jalur sepeda", "2026-11-09", "2026-11-09", today=today)) == 1
    assert len(js.search("jalur sepeda", "2026-11-09", "2026-11-09", today=today)) == 1


def test_columns_defaults_and_assume_tz_reach_the_adapter(tmp_path):
    (tmp_path / "other.csv").write_text("created_at,content,link\n2026-11-09 08:00,Banjir,https://example.com/1\n")
    config = load_config(write(tmp_path, """
data_source:
  adapter: csv
  path: other.csv
  columns: {created_at: date, content: text, link: url}
  defaults: {platform: news, source: Media Contoh}
  assume_tz: Asia/Jakarta
model: {endpoint: 'http://x/v1', name: m}
"""))
    [post] = build_adapter(config).posts
    assert post.date_wib.hour == 8 and post.source == "Media Contoh"


@pytest.mark.parametrize("model", [
    {**MODEL, "api_key": "sk-123"}, {**MODEL, "token": "abc"}, {**MODEL, "headers": {"x_secret": "s"}},
])
def test_key_like_fields_with_a_value_are_refused(tmp_path, model):
    with pytest.raises(ConfigError, match="don't put keys"):
        parse_config({"data_source": {"adapter": "csv", "path": str(ROOT / "config.example.yaml")},
                      "model": model})


def test_api_key_comes_from_the_named_environment_variable(monkeypatch):
    raw = {"data_source": {"adapter": "csv", "path": str(ROOT / "examples" / "posts_sample.csv")},
           "model": {**MODEL, "api_key_env": "SLMCP_TEST_KEY", "api_key": ""}}
    config = parse_config(raw)
    monkeypatch.delenv("SLMCP_TEST_KEY", raising=False)
    with pytest.raises(ConfigError, match="not set"):
        config.model.api_key()
    monkeypatch.setenv("SLMCP_TEST_KEY", "value-from-env")
    assert config.model.api_key() == "value-from-env"


@pytest.mark.parametrize("raw, match", [
    ([], "mapping"),
    ({"model": MODEL}, "data_source: missing"),
    ({"data_source": {"adapter": "excel", "path": "x"}, "model": MODEL}, "adapter"),
    ({"data_source": {"adapter": "csv"}, "model": MODEL}, "exactly one file"),
    ({"data_source": {"adapter": "csv", "path": "nope.csv"}, "model": MODEL}, "does not exist"),
    ({"data_source": {"adapter": "rss"}, "model": MODEL}, "rss source needs"),
    ({"data_source": {"adapter": "rss", "url": "https://example.com/rss"},
      "model": {"name": "m"}}, "endpoint"),
    ({"data_source": {"adapter": "rss", "url": "https://example.com/rss"},
      "model": {"endpoint": "http://x/v1"}}, "model.name"),
    ({"data_source": {"adapter": "rss", "url": "https://example.com/rss"}, "model": MODEL,
      "timezone": "Mars/Base"}, "time zone"),
    ({"data_source": {"adapter": "rss", "url": "https://example.com/rss", "columns": "x"}, "model": MODEL},
     "columns"),
])
def test_bad_configs_are_refused_with_the_field_name(raw, match):
    with pytest.raises(ConfigError, match=match):
        parse_config(raw)


def test_invalid_yaml_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(write(tmp_path, "data_source: [unclosed\n"))
