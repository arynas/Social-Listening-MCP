"""A short end-to-end run for CI: load config.example.yaml, build its adapter, search the synthetic
sample, and print what came back. The output is kept as a CI artifact (evidence)."""
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from slmcp import __version__  # noqa: E402
from slmcp.config import build_adapter, load_config  # noqa: E402


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
    print("proof: ok")


if __name__ == "__main__":
    main()
