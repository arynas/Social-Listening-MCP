"""A short end-to-end run for CI: load config.example.yaml, build its adapter, search the synthetic
sample, and print what came back. The output is kept as a CI artifact (evidence)."""
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import asyncio  # noqa: E402
import json  # noqa: E402

from mcp import Client  # noqa: E402

from slmcp import __version__  # noqa: E402
from slmcp.config import build_adapter, load_config  # noqa: E402
from slmcp.server import build_server, setup_logging  # noqa: E402


def main():
    config = load_config(ROOT / "config.example.yaml")
    adapter = build_adapter(config)
    print(f"social-listening-mcp {__version__}")
    name = type(adapter).__name__
    print(f"adapter: {name}, posts loaded: {len(adapter.posts)}, skipped: {adapter.skipped}")
    for keyword in ("jalur sepeda", "harga cabai", "banjir"):
        hits = adapter.search(keyword, "2026-11-01", "2026-11-09", today=date(2026, 11, 10))
        sentiment = dict(Counter(p.sentiment for p in hits))
        print(f"search {keyword!r} 2026-11-01..2026-11-09: {len(hits)} posts, sentiment {sentiment}")
        assert hits, f"no posts for {keyword!r}"
    # The same question through the MCP server, in-process: list the tools, call one, show the result.
    setup_logging(sys.stdout)
    server = build_server(config)

    async def through_mcp():
        async with Client(server) as client:
            tools = (await client.list_tools()).tools
            listed = [t.model_dump(mode="json", exclude_none=True) for t in tools]
            size = len(json.dumps(listed, ensure_ascii=False))
            print(f"mcp tools: {[t.name for t in tools]}, tool list about {size // 4} tokens")
            result = await client.call_tool("news_recap", {"keyword": "banjir", "start_date": "2026-11-01",
                                                           "end_date": "2026-11-09"})
            data = json.loads(result.content[0].text)
            print(f"mcp news_recap 'banjir': total {data['total']}, "
                  f"sentiment {data['sentiment_total']['pct']}, warnings {len(data['data_warnings'])}, "
                  f"about {data['output_tokens_estimate']} tokens")
            assert data["total"] > 0

    asyncio.run(through_mcp())
    print("proof: ok")


if __name__ == "__main__":
    main()
