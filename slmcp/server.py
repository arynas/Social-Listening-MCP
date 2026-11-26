"""MCP server with five tools (news_recap, social_recap, detect_spikes, compare_keywords, entity_trend) on
the official MCP Python SDK.

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
from pathlib import Path

from mcp.server import MCPServer

from slmcp import __version__
from slmcp.adapters.base import QueryError
from slmcp.compare import compare_keywords
from slmcp.config import build_adapter, load_config
from slmcp.recap import from_config, news_recap, social_recap
from slmcp.report import build_report_inputs
from slmcp.report_jobs import ReportJobs, ReportRenderer, add_report_routes
from slmcp.spikes import detect_spikes
from slmcp.spikes import from_config as spikes_config
from slmcp.trend import entity_trend
from slmcp.trend import from_config as trend_config

MAX_KEYWORD_CHARS = 60
MAX_KEYWORD_WORDS = 4

log = logging.getLogger("slmcp.calls")

PERIOD_RULE = """For relative periods, pass the word (e.g. period="minggu lalu"), not dates.
If the result says data_available false, tell the user the data is not available for that period,
in a polite way, and do not answer with another period."""

NEWS_DESCRIPTION = """Count and summarise Indonesian NEWS headlines that contain a keyword, for a period.
Use it for questions about news coverage or news sentiment of a topic, person or place.
- keyword: 1 or 2 words, the topic itself (e.g. "banjir", "harga beras"), not a sentence; no words like
  naik/kenaikan ("pajak", not "kenaikan pajak").
- period: a relative period word: hari ini, kemarin, minggu ini, minggu lalu, bulan ini, bulan lalu,
  7 hari terakhir, 30 hari terakhir (English also works: today, yesterday, this week, last week, ...).
- start_date, end_date: only for fixed dates the user named, YYYY-MM-DD (WIB, both included).
Report the numbers exactly as returned (do not add them up yourself; use sentiment_total).
Give only links that appear in the result. The result starts with must_tell_user: tell the user
every line of it in your answer.
""" + PERIOD_RULE

SOCIAL_DESCRIPTION = """Count and summarise SOCIAL MEDIA posts (not news) that contain a keyword,
for a period, with the most engaged posts. Use it for questions about what people post on social media.
- keyword: 1 or 2 words, the topic itself, not a sentence.
- period: a relative period word (hari ini, kemarin, minggu ini, minggu lalu, bulan ini, bulan lalu, ...).
- start_date, end_date: only for fixed dates the user named, YYYY-MM-DD (WIB, both included).
Report the numbers exactly as returned. Give only links that appear in the result.
The result starts with must_tell_user: tell the user every line of it in your answer.
""" + PERIOD_RULE


SPIKES_DESCRIPTION = """Find SPIKES: days (or hours, for 3 days or fewer) with unusually many posts about a
keyword, compared with the days just before. Use it for "kapan berita X melonjak?", not the busiest
day (use news_recap).
- keyword: 1 or 2 words. source: news (default) or social.
- period / start_date / end_date: as in news_recap.
Each spike has start, peak and end dates, the peak count, baseline median, ratio and example
headlines. Report them as returned and give only links from the result. No spikes is a valid
answer; say so. Tell the user every must_tell_user line.
""" + PERIOD_RULE

COMPARE_DESCRIPTION = """COMPARE 2 or 3 keywords over the same period: totals, sentiment, trend side by side,
differences and ratios. Use it for questions like "lebih banyak berita banjir atau gempa?".
- keywords: a list of 2 or 3 keywords, 1 or 2 words each. source: news (default) or social.
- period / start_date / end_date: as in news_recap.
Report the numbers exactly as returned; do not compute ratios or shares yourself. A headline can match
more than one keyword (overlap). Give only links from the result. Tell the user every must_tell_user line.
""" + PERIOD_RULE

TREND_DESCRIPTION = """TREND of one person, place or topic over a period: posts per day (per hour for 3
days or fewer), sentiment, the peak, the change vs the previous period of the same length, and
the top stories (similar headlines grouped, ranked by sources). Use it for the peak day, "naik
atau turun?" and "berita utama".
- keyword: 1 or 2 words. source: news (default) or social. period / start_date / end_date: as in news_recap.
Report the numbers as returned; change is null if the earlier period has no data. Give only links
from the result. Tell the user every must_tell_user line.
""" + PERIOD_RULE


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


def build_server(config, *, report_jobs=None, report_snapshot=None):
    """An MCPServer whose tools read from the config's data source."""
    adapter = build_adapter(config)
    options = from_config(config)
    server = MCPServer("social-listening-mcp", version=__version__,
                       instructions="Tools for Indonesian social listening on a fixed data source. "
                                    "Numbers are computed by the tools; report them as returned.")

    spike_options = spikes_config(config)
    tool_options = {"detect_spikes": spike_options, "entity_trend": trend_config(config)}

    def run(tool, recap_fn, keyword, period, start_date, end_date, **extra):
        started = time.monotonic()
        problem = check_keyword(keyword)
        if problem:
            result = {"error": problem}
        else:
            result = recap_fn(adapter, keyword, start_date or None, end_date or None, period=period or None,
                              **tool_options.get(tool, options), **extra)
        if "error" in result:
            error = "bad_keyword" if problem else "bad_request"
        else:
            error = None if result.get("data_available") else "no_data"
        log.info(json.dumps({
            "time": datetime.now(UTC).isoformat(timespec="seconds"), "tool": tool,
            "args": {"keyword": keyword, "period": period or None, "start_date": start_date or None,
                     "end_date": end_date or None},
            "duration_ms": round((time.monotonic() - started) * 1000), "result_count": result.get("total"),
            **({"spike_count": result.get("spike_count"), "source": extra.get("source")}
               if tool == "detect_spikes" else {}),
            **({"source": extra.get("source")} if tool == "entity_trend" else {}),
            "error": error,
        }, ensure_ascii=False))
        return result

    # Results go out as compact JSON text: the SDK would pretty-print a dict, which costs ~40% more tokens.
    @server.tool(name="news_recap", description=NEWS_DESCRIPTION, structured_output=False)
    def news_tool(keyword: str, period: str = "", start_date: str = "", end_date: str = "") -> str:
        return compact(run("news_recap", news_recap, keyword, period, start_date, end_date))

    @server.tool(name="social_recap", description=SOCIAL_DESCRIPTION, structured_output=False)
    def social_tool(keyword: str, period: str = "", start_date: str = "", end_date: str = "") -> str:
        return compact(run("social_recap", social_recap, keyword, period, start_date, end_date))

    @server.tool(name="detect_spikes", description=SPIKES_DESCRIPTION, structured_output=False)
    def spikes_tool(keyword: str, period: str = "", start_date: str = "", end_date: str = "",
                    source: str = "news") -> str:
        return compact(run("detect_spikes", detect_spikes, keyword, period, start_date, end_date,
                           source="social" if source == "social" else "news"))

    @server.tool(name="entity_trend", description=TREND_DESCRIPTION, structured_output=False)
    def trend_tool(keyword: str, period: str = "", start_date: str = "", end_date: str = "",
                   source: str = "news") -> str:
        return compact(run("entity_trend", entity_trend, keyword, period, start_date, end_date,
                           source="social" if source == "social" else "news"))

    @server.tool(name="compare_keywords", description=COMPARE_DESCRIPTION, structured_output=False)
    def compare_tool(keywords: list[str], period: str = "", start_date: str = "", end_date: str = "",
                     source: str = "news") -> str:
        started = time.monotonic()
        words = [keywords] if isinstance(keywords, str) else list(keywords or [])
        problem = next((p for p in (check_keyword(k) for k in words) if p), None)
        if problem:
            result = {"error": problem}
        else:
            result = compare_keywords(adapter, words, start_date or None, end_date or None,
                                      period=period or None,
                                      source="social" if source == "social" else "news", **options)
        if "error" in result:
            error = "bad_keyword" if problem else "bad_request"
        else:
            error = None if result.get("data_available") else "no_data"
        log.info(json.dumps({
            "time": datetime.now(UTC).isoformat(timespec="seconds"), "tool": "compare_keywords",
            "args": {"keywords": words, "period": period or None, "start_date": start_date or None,
                     "end_date": end_date or None},
            "duration_ms": round((time.monotonic() - started) * 1000),
            "result_count": {e["keyword"]: e["total"] for e in result.get("per_keyword", [])} or None,
            "source": "social" if source == "social" else "news", "error": error,
        }, ensure_ascii=False))
        return compact(result)

    if report_jobs is not None:
        add_report_tools(server, adapter, config, report_jobs, report_snapshot)
    return server


def add_report_tools(server, adapter, config, jobs, snapshot):
    @server.tool(name="build_report", structured_output=False, description=(
        "Prepare an outline for a news report. Dates have start and end in YYYY-MM-DD (WIB). Use the "
        "four-slide news-pilot template. Show the outline, warnings and approval link, then wait for the "
        "user to approve on that page. Rendering starts only after approval. Keep the report_id."
    ))
    def build_report(
        keyword: str, dates: dict[str, str], pages: int = 4, template: str = "news-pilot"
    ) -> str:
        problem = check_keyword(keyword)
        if problem:
            return compact({"error": problem})
        if pages != 4 or template != "news-pilot":
            return compact({"error": "The renderer supports four slides with the news-pilot template."})
        if set(dates) != {"start", "end"}:
            return compact({"error": "dates must contain start and end (YYYY-MM-DD)."})
        try:
            inputs = build_report_inputs(
                adapter, config, keyword, dates["start"], dates["end"], snapshot=snapshot,
            )
        except QueryError as exc:
            return compact({"error": str(exc)})
        if inputs["data_available"] and inputs["facts"]["peak"] is None:
            inputs["outline"].insert(2, {"title": "No matching headlines", "uses": ["facts.total"]})
        return compact(jobs.create(inputs))

    @server.tool(name="report_status", structured_output=False, description=(
        "Look up a report by its report_id. Show progress while queued or rendering. When ready, return "
        "the exact PowerPoint and PDF download URLs. On failure ask the user to retry in the approval page."
    ))
    def report_status(report_id: str) -> str:
        return compact(jobs.status(report_id))

    add_report_routes(server, jobs)


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
    ap.add_argument("--report-renderer", type=Path, help="path to the checked renderer's generate.py")
    ap.add_argument("--report-python", default=sys.executable, help="Python with renderer dependencies")
    ap.add_argument("--report-dir", type=Path, help="directory for report jobs and checked files")
    ap.add_argument("--report-base-url", help="browser-reachable origin for approvals and downloads")
    ap.add_argument("--report-snapshot", help="dataset version shown in report inputs")
    ap.add_argument("--report-source-name", help="name of the data source to credit")
    ap.add_argument("--report-source-url", help="source credit link checked in the exports")
    args = ap.parse_args(argv)
    setup_logging()
    jobs = None
    if args.report_renderer is not None:
        if args.transport != "streamable-http":
            ap.error("reports require streamable-http for approvals and downloads")
        if not all((args.report_dir, args.report_snapshot, args.report_source_name, args.report_source_url)):
            ap.error("set --report-dir, --report-snapshot, --report-source-name and --report-source-url")
        script = args.report_renderer.expanduser().resolve()
        if not script.is_file():
            ap.error("--report-renderer must point to an existing script")
        render = ReportRenderer(script, args.report_python, args.report_source_name, args.report_source_url)
        base_url = args.report_base_url or f"http://{args.host}:{args.port}"
        try:
            jobs = ReportJobs(args.report_dir, base_url, render)
        except ValueError as exc:
            ap.error(str(exc))
    server = build_server(load_config(args.config), report_jobs=jobs, report_snapshot=args.report_snapshot)
    print(f"social-listening-mcp {__version__} starting ({args.transport})", file=sys.stderr, flush=True)
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
