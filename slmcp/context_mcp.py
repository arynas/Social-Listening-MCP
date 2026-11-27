"""Serve web search or passage retrieval using read-only MCP tools."""

import argparse
import json
import re
from functools import partial
from html import unescape
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from slmcp.analyst import NewsRequest
from slmcp.context import LIMIT, in_scope, source_passage


def tokens(text):
    return set(re.findall(r"[^\W_]+", text.casefold()))


def load_passages(path):
    return [source_passage(doc) for doc in json.loads(path.read_text(encoding="utf-8"))]


def search_passages(passages, request):
    terms = tokens(request.keyword)
    matches = []
    for doc in passages:
        if not in_scope(doc, request.start, request.end):
            continue
        score = 2 * len(terms & tokens(doc["title"])) + len(terms & tokens(doc["excerpt"]))
        if score:
            matches.append((score, doc))
    matches.sort(key=lambda match: -match[0])
    return [doc for _, doc in matches[:LIMIT]]


def search_public_posts(request, endpoint, publisher):
    params = urlencode({"search": request.keyword, "per_page": LIMIT, "orderby": "relevance",
                        "_fields": "link,title,excerpt,date", "after": f"{request.start}T00:00:00",
                        "before": f"{request.end}T23:59:59"})
    call = Request(f"{endpoint}?{params}", headers={"User-Agent": "slmcp-context"})
    with urlopen(call, timeout=12) as response:
        rows = json.loads(response.read(250_000))
    if not isinstance(rows, list):
        raise ValueError("Expected a WordPress list of posts")
    documents = []
    for row in rows[:LIMIT]:
        title = unescape(re.sub(r"<[^>]+>", "", row["title"]["rendered"])).strip()
        excerpt = unescape(re.sub(r"<[^>]+>", "", row["excerpt"]["rendered"])).strip()
        if " – " in excerpt[:60]:
            excerpt = excerpt.split(" – ", 1)[1]
        words = excerpt.split()
        excerpt = " ".join(words[:24]) + (" …" if len(words) > 24 else "")
        documents.append(source_passage({
            "title": title[:250], "publisher": publisher, "url": row["link"],
            "section": "Ringkasan artikel", "excerpt": excerpt,
            "date": row["date"][:10], "date_kind": "published", "scope": "event",
        }))
    return documents


def create_server(web=None, kb=None):
    server = MCPServer("Analyst context")

    if web is not None:
        @server.tool(annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)
        def search_web(query: str, start: str, end: str) -> str:
            """Find public web passages or indexed headlines in the requested period."""
            return json.dumps({"passages": web(NewsRequest(query, start, end))}, ensure_ascii=False)

    if kb is not None:
        @server.tool(annotations=ToolAnnotations(readOnlyHint=True), structured_output=False)
        def retrieve_knowledge(query: str, start: str, end: str) -> str:
            """Retrieve dated passages from the configured knowledge base."""
            return json.dumps({"passages": kb(NewsRequest(query, start, end))}, ensure_ascii=False)

    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    providers = parser.add_mutually_exclusive_group()
    providers.add_argument("--web-index", type=Path, help="JSON passages captured from public web sources")
    providers.add_argument("--web-posts-url", help="public WordPress REST posts endpoint")
    parser.add_argument("--web-publisher", help="publisher name for live search citations")
    parser.add_argument("--kb-index", type=Path, action="append",
                        help="JSON passages in the local knowledge base (repeatable)")
    parser.add_argument("--port", type=int, default=8772)
    args = parser.parse_args(argv)
    if not (args.web_index or args.kb_index or args.web_posts_url):
        parser.error("Configure at least one context provider")
    web = None
    if args.web_posts_url:
        parts = urlsplit(args.web_posts_url)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.query or parts.fragment):
            parser.error("Use a public HTTPS posts endpoint without credentials, query, or fragment")
        web = partial(search_public_posts, endpoint=args.web_posts_url, publisher=args.web_publisher
                      or parts.hostname)
    kb = None
    if args.web_index:
        web_index = load_passages(args.web_index)

        def web(request):
            return search_passages(web_index, request)

    if args.kb_index:
        kb_index = [doc for file in args.kb_index for doc in load_passages(file)]

        def kb(request):
            return search_passages(kb_index, request)

    create_server(web, kb).run("streamable-http", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
