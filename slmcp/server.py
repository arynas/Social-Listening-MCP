"""MCP server with two tools, news_recap and social_recap, built on the official MCP Python SDK.

    slmcp-server --config config.snapshot.example.yaml              # stdio (default)
    slmcp-server --config config.yaml --transport streamable-http   # HTTP on 127.0.0.1:8000/mcp

The data source, the frozen "today" and the sentiment-label note come from the YAML config.
Every call writes one JSON line to stderr (stdout is the MCP channel in stdio mode): time, tool,
arguments, duration, result count and error class. Post text is never logged.
"""
import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime

from mcp.server import MCPServer

from slmcp import __version__
from slmcp.config import build_adapter, load_config
from slmcp.recap import from_config, news_recap, social_recap

MAX_KEYWORD_CHARS = 60
MAX_KEYWORD_WORDS = 4

log = logging.getLogger("slmcp.calls")

NEWS_DESCRIPTION = """Count and summarise Indonesian NEWS headlines that contain a keyword, for a date range.
Use it for questions about news coverage or news sentiment of a topic, person or place.
- keyword: 1 or 2 words, the topic itself (e.g. "banjir", "harga beras"), not a sentence.
- start_date, end_date: real dates YYYY-MM-DD (WIB, both included). For words like "kemarin" or
  "minggu lalu", use the exact dates in relative_dates of any earlier result, or ask the user.
Report the numbers exactly as returned (do not add them up yourself; use sentiment_total).
Give only links that appear in the result. Always tell the user every item in data_warnings."""

SOCIAL_DESCRIPTION = """Count and summarise SOCIAL MEDIA posts (not news) that contain a keyword,
for a date range, with the most engaged posts. Use it for questions about what people post on social media.
- keyword: 1 or 2 words, the topic itself, not a sentence.
- start_date, end_date: real dates YYYY-MM-DD (WIB, both included); use relative_dates for words
  like "kemarin" or "minggu lalu".
Report the numbers exactly as returned. Give only links that appear in the result.
Always tell the user every item in data_warnings."""


def check_keyword(keyword):
    """A short error message for keywords the tools can't use, or None if the keyword is fine."""
    words = str(keyword or "").split()
    if not words:
        return "keyword: empty; give the topic in 1 or 2 words, e.g. banjir"
    if len(words) > MAX_KEYWORD_WORDS or len(" ".join(words)) > MAX_KEYWORD_CHARS:
        return (f"keyword: too long ({len(words)} words); use the topic in 1 or 2 words, e.g. "
                f"'{' '.join(words[:2])}', not a full question")
    return None


def compact(result):
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


def build_server(config):
    """An MCPServer whose two tools read from the config's data source."""
    adapter = build_adapter(config)
    options = from_config(config)
    server = MCPServer("social-listening-mcp", version=__version__,
                       instructions="Tools for Indonesian social listening on a fixed data source. "
                                    "Numbers are computed by the tools; report them as returned.")

    def run(tool, recap_fn, keyword, start_date, end_date):
        started = time.monotonic()
        problem = check_keyword(keyword)
        if problem:
            result = {"error": problem}
        else:
            result = recap_fn(adapter, keyword, start_date, end_date, **options)
        log.info(json.dumps({
            "time": datetime.now(UTC).isoformat(timespec="seconds"), "tool": tool,
            "args": {"keyword": keyword, "start_date": start_date, "end_date": end_date},
            "duration_ms": round((time.monotonic() - started) * 1000), "result_count": result.get("total"),
            "error": None if "error" not in result else ("bad_keyword" if problem else "bad_request"),
        }, ensure_ascii=False))
        return result

    # Results go out as compact JSON text: the SDK would pretty-print a dict, which costs ~40% more tokens.
    @server.tool(name="news_recap", description=NEWS_DESCRIPTION, structured_output=False)
    def news_tool(keyword: str, start_date: str, end_date: str) -> str:
        return compact(run("news_recap", news_recap, keyword, start_date, end_date))

    @server.tool(name="social_recap", description=SOCIAL_DESCRIPTION, structured_output=False)
    def social_tool(keyword: str, start_date: str, end_date: str) -> str:
        return compact(run("social_recap", social_recap, keyword, start_date, end_date))

    return server


def setup_logging(stream=None):
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.handlers[:] = [handler]
    log.setLevel(logging.INFO)
    log.propagate = False


def main(argv=None):
    ap = argparse.ArgumentParser(description="social-listening-mcp server")
    ap.add_argument("--config", required=True, help="path to the YAML config")
    ap.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)
    setup_logging()
    server = build_server(load_config(args.config))
    print(f"social-listening-mcp {__version__} starting ({args.transport})", file=sys.stderr, flush=True)
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
