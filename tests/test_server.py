import asyncio
import io
import json
from pathlib import Path

import pytest
from mcp import Client

from slmcp.config import parse_config
from slmcp.server import build_server, check_keyword, main, setup_logging

ROOT = Path(__file__).parent.parent
SAMPLE = ROOT / "examples" / "posts_sample.csv"


def server_and_log(today="2026-11-10"):
    source = {"adapter": "csv", "path": str(SAMPLE), "sentiment_labels": "model"}
    config = parse_config({"data_source": source, "today": today,
                           "model": {"endpoint": "http://localhost:8000/v1", "name": "m"}})
    stream = io.StringIO()
    setup_logging(stream)
    return build_server(config), stream


def call(server, name, args):
    async def go():
        async with Client(server) as client:
            result = await client.call_tool(name, args)
            return result.is_error, json.loads(result.content[0].text)
    return asyncio.run(go())


def log_lines(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_lists_both_tools_with_instructions_and_a_small_schema():
    server, _ = server_and_log()

    async def go():
        async with Client(server) as client:
            return (await client.list_tools()).tools
    tools = asyncio.run(go())
    assert sorted(t.name for t in tools) == ["news_recap", "social_recap"]
    for t in tools:
        assert sorted(t.input_schema["required"]) == ["end_date", "keyword", "start_date"]
        assert "YYYY-MM-DD" in t.description and "must_tell_user" in t.description
        # the frozen today comes first, with the exact dates of the common time words
        first = t.description.splitlines()[0]
        assert first.startswith("Today is Tuesday 10 November 2026 (WIB). Use this date, not your own clock")
        assert "kemarin = 2026-11-09" in first and "minggu lalu = 2026-11-02..2026-11-08" in first
    size = len(json.dumps([t.model_dump(mode="json", exclude_none=True) for t in tools], ensure_ascii=False))
    assert size // 4 < 700  # the whole tool list stays small (chars / 4 as a token estimate)


def test_without_a_frozen_today_the_descriptions_use_the_real_wib_date():
    from datetime import datetime

    from slmcp.recap import today_line
    from slmcp.schema import WIB
    server, _ = server_and_log(today=None)

    async def go():
        async with Client(server) as client:
            return (await client.list_tools()).tools
    real = today_line(datetime.now(WIB).date())
    assert all(t.description.startswith(real) for t in asyncio.run(go()))


def test_must_tell_user_comes_first_and_repeats_the_warnings_as_sentences():
    server, _ = server_and_log()
    _, result = call(server, "news_recap", {"keyword": "jalur sepeda", "start_date": "2026-11-01",
                                            "end_date": "2026-11-09"})
    assert list(result)[0] == "must_tell_user"
    assert len(result["must_tell_user"]) == len(result["data_warnings"]) > 0
    for line, warning in zip(result["must_tell_user"], result["data_warnings"], strict=True):
        assert line[0].isupper() and line.endswith(".") and line[1:-1] == warning[1:]


def test_news_recap_through_mcp_and_one_log_line_without_post_text():
    server, stream = server_and_log()
    is_error, result = call(server, "news_recap", {"keyword": "jalur sepeda", "start_date": "2026-11-01",
                                                   "end_date": "2026-11-09"})
    assert not is_error and result["total"] > 0 and result["today"] == "2026-11-10"
    assert round(sum(result["sentiment_total"]["pct"].values()), 1) == 100.0
    assert any("rough model output" in w for w in result["data_warnings"])
    [line] = log_lines(stream)
    assert line["tool"] == "news_recap" and line["result_count"] == result["total"] and line["error"] is None
    assert set(line) == {"time", "tool", "args", "duration_ms", "result_count", "error"}
    assert "Jalur sepeda" not in stream.getvalue()  # no post text in the log


def test_social_recap_through_mcp_has_top_posts():
    server, _ = server_and_log()
    _, result = call(server, "social_recap", {"keyword": "banjir", "start_date": "2026-11-01",
                                              "end_date": "2026-11-09"})
    assert result["total"] > 0 and "news" not in [p["name"] for p in result["top_platforms"]]
    assert result["top_posts"]


@pytest.mark.parametrize("args, error, error_class", [
    ({"keyword": "", "start_date": "2026-11-01", "end_date": "2026-11-09"}, "keyword: empty", "bad_keyword"),
    ({"keyword": "bagaimana sentimen berita banjir minggu lalu", "start_date": "2026-11-01",
      "end_date": "2026-11-09"}, "too long", "bad_keyword"),
    ({"keyword": "banjir", "start_date": "minggu lalu", "end_date": "2026-11-09"}, "YYYY-MM-DD",
     "bad_request"),
    ({"keyword": "banjir", "start_date": "2026-11-09", "end_date": "2026-11-01"}, "after end", "bad_request"),
    ({"keyword": "banjir", "start_date": "2026-11-01", "end_date": "2026-11-20"}, "future", "bad_request"),
])
def test_bad_calls_return_a_clear_error_and_are_logged(args, error, error_class):
    server, stream = server_and_log()
    _, result = call(server, "news_recap", args)
    assert error in result["error"]
    [line] = log_lines(stream)
    assert line["error"] == error_class and line["result_count"] is None


def test_bad_date_error_includes_exact_dates():
    server, _ = server_and_log(today="2026-11-10")
    _, result = call(server, "news_recap", {"keyword": "banjir", "start_date": "kemarin",
                                            "end_date": "kemarin"})
    assert result["relative_dates"]["kemarin"] == ["2026-11-09", "2026-11-09"]


def test_check_keyword():
    assert check_keyword("banjir") is None and check_keyword("harga beras") is None
    assert "empty" in check_keyword("   ")


def test_entry_point_needs_a_config():
    with pytest.raises(SystemExit):
        main([])
