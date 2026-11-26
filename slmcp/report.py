"""Prepare verifiable inputs and a proposed outline for a one-topic news report."""
import argparse
import json

from slmcp.adapters.base import QueryError
from slmcp.config import ConfigError, build_adapter, load_config
from slmcp.recap import from_config as recap_config
from slmcp.recap import news_recap
from slmcp.trend import entity_trend
from slmcp.trend import from_config as trend_config


def build_report_inputs(adapter, config, keyword, start_date, end_date, *, snapshot):
    """Collect facts from the existing news analyses. Outline sections refer to those facts by key."""
    if not (keyword or "").strip():
        raise QueryError("keyword: empty; give a word or name to search for")
    recap = news_recap(adapter, keyword, start_date, end_date, **recap_config(config))
    if "error" in recap:
        raise QueryError(recap["error"])

    available = recap["data_available"]
    period = (recap["start_date"], recap["end_date"]) if available else (
        recap["asked_period"]["start"], recap["asked_period"]["end"]
    )
    outline = [{"title": "Data coverage", "uses": ["query", "warnings"]}]
    report = {
        "query": {"keyword": recap["keyword"], "start_date": period[0], "end_date": period[1],
                  "timezone": "Asia/Jakarta", "snapshot": snapshot},
        "data_available": available,
        "facts": {},
        "fact_sources": {},
        "warnings": recap["must_tell_user"],
        "outline": outline,
    }
    if not available:
        return report

    trend = entity_trend(
        adapter, recap["keyword"], *period, unit="day", max_tokens=None, **trend_config(config)
    )
    if "error" in trend:
        raise QueryError(trend["error"])
    if not trend["data_available"] or trend["total"] != recap["total"]:
        raise QueryError("news_recap and entity_trend disagree on the headline total")

    series = trend["trend"]
    daily = [{"date": day, "count": n} for day, n in zip(series["labels"], series["total"], strict=True)]
    if sum(point["count"] for point in daily) != recap["total"]:
        raise QueryError("daily headline counts do not add up to the headline total")

    change = trend["change"]
    if change is not None:
        change = {key: change[key] for key in ("previous_start", "previous_end", "previous_total",
                                              "total_diff", "pct_change")}
    report["facts"] = {
        "total": recap["total"],
        "daily_trend": daily,
        "peak": trend["peak"],
        "change": change,
        "top_stories": trend["top_stories"],
    }
    report["fact_sources"] = {"total": "news_recap.total", "daily_trend": "entity_trend.trend",
                              "peak": "entity_trend.peak", "change": "entity_trend.change",
                              "top_stories": "entity_trend.top_stories"}
    report["warnings"] = trend["must_tell_user"]
    volume_refs = ["facts.total", "facts.daily_trend"]
    if change is not None:
        volume_refs.append("facts.change")
    outline.append({"title": "Headline volume and daily trend", "uses": volume_refs})
    if trend["peak"]:
        outline.append({"title": "Peak and example headlines", "uses": ["facts.peak", "facts.top_stories"]})
    outline.append({"title": "Data limitations", "uses": ["warnings"]})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="path to the YAML config")
    parser.add_argument("--keyword", required=True, help="one news topic or phrase")
    parser.add_argument("--start", required=True, help="first WIB date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="last WIB date (YYYY-MM-DD)")
    parser.add_argument("--snapshot", required=True, help="dataset name or version to record in the report")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        report = build_report_inputs(build_adapter(config), config, args.keyword, args.start, args.end,
                                     snapshot=args.snapshot)
    except (ConfigError, OSError, QueryError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
