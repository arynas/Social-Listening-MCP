"""Collect and cite optional context without changing the snapshot's facts."""

import asyncio
import json
from datetime import date, datetime
from html import escape
from urllib.parse import urlsplit

import httpx2
from mcp import ClientSession, MCPError
from mcp.client.streamable_http import streamable_http_client

TOOLS = {"web": "search_web", "kb": "retrieve_knowledge"}
LABELS = {"web": "Pencarian web", "kb": "Basis pengetahuan"}
LIMIT = 3


def source_passage(raw):
    if not isinstance(raw, dict) or type(raw.get("synthetic", False)) is not bool:
        raise ValueError("Invalid source passage")
    fields = {"title": 250, "publisher": 120, "section": 160, "excerpt": 600}
    passage = {}
    for field, limit in fields.items():
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"Invalid citation {field}")
        passage[field] = " ".join(value.split())

    kind, when = raw.get("date_kind"), raw.get("date")
    if kind not in ("published", "indexed", "publication_month") or not isinstance(when, str):
        raise ValueError("Invalid citation date")
    formatted = (datetime.strptime(when, "%Y-%m").strftime("%Y-%m")
                 if kind == "publication_month" else date.fromisoformat(when).isoformat())
    if formatted != when:
        raise ValueError("Invalid citation date format")
    scope = raw.get("scope")
    if scope not in ("event", "background") or (scope == "event" and len(when) != 10):
        raise ValueError("Invalid citation scope")

    url, synthetic = raw.get("url"), raw.get("synthetic", False)
    if url is not None:
        if (not isinstance(url, str) or len(url) > 1500
                or any(char.isspace() or char in "<>\\" for char in url)):
            raise ValueError("Invalid citation URL")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            raise ValueError("Invalid citation URL")
    elif not synthetic:
        raise ValueError("Public passages require a citation URL")
    passage.update(date=when, date_kind=kind, scope=scope, url=url, synthetic=synthetic)
    observation = raw.get("observation")
    if observation is not None:
        if (scope != "event" or not isinstance(observation, dict)
                or set(observation) != {"topic", "date", "value"}
                or any(not isinstance(value, str) or not value.strip() or len(value) > 150
                       for value in observation.values())
                or date.fromisoformat(observation["date"]).isoformat() != observation["date"]):
            raise ValueError("Invalid comparable observation")
        passage["observation"] = observation
    return passage


async def collect_one(kind, endpoint, request, record):
    tool = TOOLS[kind]
    if endpoint is None:
        return "disabled", []
    args = {"query": request.keyword, "start": request.start, "end": request.end}
    record("tool_call", tool=tool, arguments=args)
    try:
        async with asyncio.timeout(20), streamable_http_client(endpoint) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=16) as session:
                await session.initialize()
                reply = await session.call_tool(tool, args)
        if reply.is_error or len(reply.content) != 1 or reply.content[0].type != "text":
            raise ValueError("Context tool failed")
        result = json.loads(reply.content[0].text)
        passages = result.get("passages") if isinstance(result, dict) else None
        if not isinstance(passages, list) or len(passages) > LIMIT:
            raise ValueError("Invalid context response")
        documents = [source_passage(passage) for passage in passages]
        documents = [doc for doc in documents if in_scope(doc, request.start, request.end)]
    except (OSError, ValueError, httpx2.HTTPError, MCPError, ExceptionGroup) as error:
        record("tool_result", tool=tool, status="unavailable", error_type=type(error).__name__)
        return "unavailable", []
    state = "ok" if documents else "empty"
    record("tool_result", tool=tool, status=state, passages=len(documents))
    return state, documents


def in_scope(doc, start, end):
    if doc["scope"] == "background":
        return doc["date"] <= end
    when = doc.get("observation", {}).get("date", doc["date"])
    return start <= when <= end and doc["date"] <= end


async def collect_context(request, web_url, kb_url, record):
    responses = await asyncio.gather(collect_one("web", web_url, request, record),
                                     collect_one("kb", kb_url, request, record))
    states = {}
    sources = {}
    for kind, (state, passages) in zip(TOOLS, responses, strict=True):
        states[kind] = state
        for i, passage in enumerate(passages, 1):
            sources[f"{kind}-{i}"] = passage
    return {"services": states, "sources": sources, "notices": context_notices(states, sources)}


def context_notices(states, sources):
    notices = [
        {"kind": state, "text": f"{LABELS[kind]} "
         + ("tidak tersedia saat pengambilan sumber." if state == "unavailable"
            else "tidak menemukan sumber yang relevan dalam cakupan."), "evidence": []}
        for kind, state in states.items() if state in ("unavailable", "empty")
    ]
    groups = {}
    for ref, doc in sources.items():
        observation = doc.get("observation")
        if observation:
            key = (observation["topic"], observation["date"])
            groups.setdefault(key, {})[ref] = observation["value"]
    for (topic, when), observations in groups.items():
        if len(set(observations.values())) > 1:
            notices.append({"kind": "conflict", "text": f"Sumber berselisih mengenai {topic} ({when}). "
                            "Kesimpulan mengenai hal ini ditahan.", "evidence": list(observations)})
    return notices


def usable_sources(context):
    disputed = {ref for notice in context["notices"] if notice["kind"] == "conflict"
                for ref in notice["evidence"]}
    return {ref: doc for ref, doc in context["sources"].items()
            if ref not in disputed and not doc["synthetic"]}


def escape_markdown(text):
    return escape(text).replace("[", r"\[").replace("]", r"\]")


def brief_markdown(brief):
    context = brief["context"]
    evidence = context["evidence"]
    lines = ["# Ringkasan analisis", "", "## Temuan snapshot", ""]
    for finding in brief["findings"]:
        refs = ", ".join(finding["evidence"])
        lines.append(f"- {escape_markdown(finding['text'])} (bukti snapshot {refs})")
    lines.append("\n[Sumber snapshot data-2026-10-v2]"
                 "(https://github.com/arynas/Social-Listening-MCP/releases/tag/data-2026-10-v2)")
    if context["findings"] or evidence["notices"]:
        lines.extend(("", "## Konteks dan batasan", ""))
        for item in (*context["findings"], *evidence["notices"]):
            refs = ", ".join(f"[{ref}](#{ref})" for ref in item["evidence"])
            lines.append(f"- {escape_markdown(item['text'])}" + (f" ({refs})" if refs else ""))
    if evidence["sources"]:
        lines.extend(("", "## Sumber konteks", ""))
        for ref, doc in evidence["sources"].items():
            url = doc["url"]
            link = f" [Sumber](<{url}>)" if url else ""
            if doc["synthetic"]:
                link += " · Sumber sintetis"
            date_kind = {"indexed": "diindeks", "published": "terbit", "publication_month": "bulan terbit"}
            title, publisher, section, excerpt = (
                escape_markdown(doc[key]) for key in ("title", "publisher", "section", "excerpt")
            )
            when = f"{date_kind[doc['date_kind']]} {doc['date']}"
            lines.extend((f'<a id="{ref}"></a>', f"**{ref}** · {publisher} · {title} ({when}).{link}",
                          "", f"{section} · {excerpt}", ""))
    return "\n".join(lines) + "\n"
