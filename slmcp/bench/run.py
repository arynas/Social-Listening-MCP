"""Model runner: ask each benchmark question to a model over an OpenAI-compatible chat endpoint, with the
two MCP tools available, and record the answer and every tool call as JSONL.

    export SLMCP_BENCH_BASE_URL=http://localhost:8000/v1   # any OpenAI-compatible server
    export SLMCP_BENCH_API_KEY=...                          # optional; never written anywhere
    export SLMCP_BENCH_MODEL=Qwen3.6-27B
    export SLMCP_BENCH_EXTRA='{"chat_template_kwargs": {"enable_thinking": false}}'   # optional
    export SLMCP_BENCH_USER_AGENT=...                       # optional, for gateways that filter clients
    python -m slmcp.bench.run --out runs/qwen-run1.jsonl --sample 10 --seed 1

The tools are the real MCP tools (in-process MCP client on the same server code as slmcp-server).
Each question starts a fresh conversation. No system prompt by default (like a plain chat client).
"""
import argparse
import asyncio
import http.client
import io
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from mcp import Client

from slmcp.bench.check import load_items
from slmcp.config import load_config
from slmcp.server import build_server, setup_logging

MAX_ROUNDS = 5
ABLATIONS = ("descriptions", "sentiment_total", "relative_dates")
SHORT_DESCRIPTIONS = {"news_recap": "Count Indonesian news headlines that contain a keyword in a date range.",
                      "social_recap": "Count social media posts that contain a keyword in a date range."}
DATES_ONLY_ERROR = ("start_date and end_date must be real dates (YYYY-MM-DD, WIB); "
                    "period words are not accepted")


def strip_periods(description):
    """A tool description without the period-word parts (for the relative_dates ablation)."""
    keep, in_period = [], False
    for line in description.splitlines():
        if in_period and line.startswith("  "):
            continue
        in_period = line.startswith("- period:")
        if in_period or line.startswith("For relative periods"):
            continue
        keep.append(line.replace("only for fixed dates the user named, YYYY-MM-DD", "real dates YYYY-MM-DD"))
    return "\n".join(keep)


def openai_tools(tools, ablate=None):
    """The MCP tools as OpenAI function definitions, optionally changed for an ablation."""
    out = []
    for t in tools:
        description, params = t.description, json.loads(json.dumps(t.input_schema))
        if ablate == "descriptions":
            description = SHORT_DESCRIPTIONS.get(t.name, description.splitlines()[0])
        if ablate == "relative_dates":
            description = strip_periods(description)
            params.get("properties", {}).pop("period", None)
        if ablate == "sentiment_total":
            description = description.replace(" (do not add them up yourself; use sentiment_total)", "")
        out.append({"type": "function", "function": {"name": t.name, "description": description,
                                                     "parameters": params}})
    return out


def ablate_call(args, ablate):
    """For the relative_dates ablation, refuse period words before they reach the server."""
    if ablate != "relative_dates" or not isinstance(args, dict):
        return None
    from slmcp.recap import period_key
    if args.get("period") or period_key(args.get("start_date")) or period_key(args.get("end_date")):
        return json.dumps({"error": DATES_ONLY_ERROR})
    return None


def ablate_result(text, ablate):
    """Remove a field from a tool result for an ablation (sentiment_total, relative_dates)."""
    field = {"sentiment_total": "sentiment_total", "relative_dates": "relative_dates"}.get(ablate)
    if not field:
        return text
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    if isinstance(data, dict) and field in data:
        data.pop(field)
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return text


def http_chat(base_url, model, api_key=None, extra=None, timeout=600, user_agent=None):
    """A function messages, tools -> assistant message (dict), for an OpenAI-compatible endpoint."""
    headers = {"Content-Type": "application/json", "User-Agent": user_agent or "slmcp-bench"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    url = f"{base_url.rstrip('/')}/chat/completions"

    def chat(messages, tools):
        body = {"model": model, "messages": messages, "tools": tools, "temperature": 0, **(extra or {})}
        req = urllib.request.Request(url, json.dumps(body).encode(), headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - URL comes from the user's env
            data = json.loads(r.read())
        msg = data["choices"][0]["message"]
        msg["_usage"] = data.get("usage") or {}
        return msg
    return chat


async def ask(client, tools, chat, question, system=None, max_rounds=MAX_ROUNDS, ablate=None):
    """One question, one fresh conversation. Returns answer, tool calls, timing, token use and the largest
    prompt of any single round (max_prompt_tokens, to check whether the item would fit a smaller context)."""
    messages = [{"role": "system", "content": system}] if system else []
    messages.append({"role": "user", "content": question})
    calls, usage, started = [], {"prompt_tokens": 0, "completion_tokens": 0}, time.monotonic()
    answer, stopped, max_prompt = "", "max_rounds", 0
    for _ in range(max_rounds):
        msg = await asyncio.to_thread(chat, messages, tools)
        used = msg.pop("_usage", None) or {}
        for k in usage:
            usage[k] += used.get(k) or 0
        max_prompt = max(max_prompt, used.get("prompt_tokens") or 0)
        tool_calls = msg.get("tool_calls") or []
        messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
        if not tool_calls:
            answer, stopped = msg.get("content") or "", "answer"
            break
        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
                text = ablate_call(args, ablate)
                if text is None:
                    res = await client.call_tool(name, args)
                    text = ablate_result(res.content[0].text if res.content else "", ablate)
            except Exception as e:  # a bad call is recorded and shown to the model, not a crash
                args, text = tc["function"].get("arguments"), json.dumps({"error": f"tool call failed: {e}"})
            calls.append({"name": name, "args": args, "result": text})
            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": text})
    return {"answer": answer, "tool_calls": calls, "stopped": stopped,
            "seconds": round(time.monotonic() - started, 1), "usage": usage, "max_prompt_tokens": max_prompt}


async def run_items(config_path, items, chat, out_path, system=None, model="", ablate=None):
    server = build_server(load_config(config_path))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    async with Client(server) as client:
        tools = openai_tools((await client.list_tools()).tools, ablate)
        with out.open("a", encoding="utf-8") as f:
            for item in items:
                try:
                    rec = await ask(client, tools, chat, item["question"], system, ablate=ablate)
                except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
                    rec = {"answer": "", "tool_calls": [], "stopped": f"http_error: {type(e).__name__}",
                           "seconds": None, "usage": {}}
                f.write(json.dumps({"id": item["id"], "model": model, "ablation": ablate, **rec},
                                   ensure_ascii=False) + "\n")
                f.flush()
                n = len(rec["tool_calls"])
                print(f"{item['id']}: {rec['stopped']}, {n} tool calls, {rec['seconds']} s", flush=True)


def pick(items, ids=None, sample=None, seed=1):
    if ids:
        wanted = set(ids)
        return [i for i in items if i["id"] in wanted]
    if sample:
        return random.Random(seed).sample(items, sample)
    return items


def main(argv=None):
    ap = argparse.ArgumentParser(description="run benchmark questions against a model with the MCP tools")
    ap.add_argument("--config", default="config.snapshot.example.yaml")
    ap.add_argument("--questions", default="benchmark/questions-v1.1.jsonl")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--sample", type=int)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--system", default=None, help="optional system prompt (default: none)")
    ap.add_argument("--ablate", choices=ABLATIONS, default=None,
                    help="descriptions: one-line tool descriptions; sentiment_total: drop that field from "
                         "results; relative_dates: no period words and no relative_dates field")
    args = ap.parse_args(argv)
    base, model = os.environ.get("SLMCP_BENCH_BASE_URL"), os.environ.get("SLMCP_BENCH_MODEL", "")
    if not base:
        sys.exit("set SLMCP_BENCH_BASE_URL (and SLMCP_BENCH_MODEL, optional SLMCP_BENCH_API_KEY)")
    extra = json.loads(os.environ.get("SLMCP_BENCH_EXTRA") or "{}")
    chat = http_chat(base, model, os.environ.get("SLMCP_BENCH_API_KEY"), extra,
                     user_agent=os.environ.get("SLMCP_BENCH_USER_AGENT"))
    setup_logging(io.StringIO())
    items = pick(load_items(args.questions), args.ids, args.sample, args.seed)
    asyncio.run(run_items(args.config, items, chat, args.out, args.system, model, args.ablate))


if __name__ == "__main__":
    main()
