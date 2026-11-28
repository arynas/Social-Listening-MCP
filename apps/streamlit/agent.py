"""Tool-calling loop for the Streamlit demo, kept free of Streamlit so it can be tested.

The model only chooses tools and writes text. Every number comes from the MCP tools on the
running server; this module passes their JSON results back to the model unchanged.
"""

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

DATA_TOOLS = ("news_recap", "compare_keywords", "entity_trend", "detect_spikes", "social_recap")
REPORT_TOOLS = ("build_report", "report_status")
REVIEW_TOOL = "get_analyst_final_review"
MAX_STEPS = 6

SYSTEM_PROMPT = (
    "You are a news-data assistant. Use the tools for every count, share, trend or date range; "
    "never compute or guess numbers yourself. Report numbers exactly as the tool returns them and "
    "always pass on the tool's warnings. If the tool says the period is outside the data, explain the "
    "covered dates and offer another period instead of giving numbers. Answer in the user's language."
)


class AgentError(RuntimeError):
    """A model or tool call failed in a way the user should see."""


@dataclass(frozen=True)
class ModelProfile:
    label: str
    base_url: str
    model: str
    key_env: str | None = None
    thinking_off: bool = True

    def key(self):
        return os.environ.get(self.key_env, "") if self.key_env else ""


@dataclass(frozen=True)
class Settings:
    mcp_url: str
    review_mcp_url: str
    profiles: tuple[ModelProfile, ...] = field(default_factory=tuple)


def load_settings(env=None):
    """Read endpoints from the environment. Keys are read later, by name, and never stored here."""
    env = os.environ if env is None else env
    profiles = [ModelProfile(
        label="Qwen3.6-27B",
        base_url=env.get("SLMCP_QWEN_BASE_URL", "http://127.0.0.1:8000/v1"),
        model=env.get("SLMCP_QWEN_MODEL", "Qwen3.6-27B-Q4_K_M.gguf"),
        key_env=env.get("SLMCP_QWEN_KEY_ENV") or None,
        thinking_off=True,
    )]
    if env.get("TORCONS_BASE_URL"):
        profiles.append(ModelProfile(
            label="Torcons-Pax",
            base_url=env["TORCONS_BASE_URL"],
            model=env.get("TORCONS_MODEL", env.get("MODEL_NAME", "Torcons-Pax")),
            key_env="TORCONS_API_KEY",
            thinking_off=False,
        ))
    return Settings(
        mcp_url=env.get("SLMCP_MCP_URL", "http://127.0.0.1:8769/mcp"),
        review_mcp_url=env.get("SLMCP_REVIEW_MCP_URL", "http://127.0.0.1:8771/mcp"),
        profiles=tuple(profiles),
    )


def openai_chat(profile, *, timeout=180):
    """Return chat(messages, tools) for an OpenAI-compatible endpoint."""
    parts = urlsplit(profile.base_url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise AgentError("Use an HTTP(S) model URL without inline credentials")
    headers = {"Content-Type": "application/json", "User-Agent": "slmcp-streamlit"}
    key = profile.key()
    if key:
        headers["Authorization"] = f"Bearer {key}"

    def chat(messages, tools):
        payload = {"model": profile.model, "messages": messages, "temperature": 0, "max_tokens": 1500}
        if tools:
            payload["tools"] = tools
        if profile.thinking_off:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        request = Request(f"{profile.base_url.rstrip('/')}/chat/completions",
                          data=json.dumps(payload).encode(), headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                body = json.load(response)
        except HTTPError as error:
            raise AgentError(f"The model endpoint refused the request: HTTP {error.code} "
                             f"{error_message(error)}") from error
        except OSError as error:
            raise AgentError(f"The model endpoint did not answer: {type(error).__name__}") from error
        try:
            return body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as error:
            raise AgentError("The model endpoint returned an unexpected reply") from error

    return chat


def error_message(error):
    """The endpoint's own short error message, e.g. 'Invalid API Key', never request data."""
    try:
        body = json.loads(error.read(2000))
        problem = body.get("error")
        message = problem.get("message") if isinstance(problem, dict) else problem
    except (OSError, ValueError, AttributeError):
        return ""
    if not message:
        return ""
    # Hosted gateways put account IDs in rate-limit messages; keep them off the screen.
    masked = re.sub(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}\S*", "<account>", str(message))
    return f"({masked[:120]})"


async def _list_tools(url):
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=30) as session:
            await session.initialize()
            return (await session.list_tools()).tools


async def _call_tool(url, name, arguments):
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=120) as session:
            await session.initialize()
            reply = await session.call_tool(name, arguments)
    text = "\n".join(part.text for part in reply.content if getattr(part, "type", "") == "text")
    return text, bool(reply.is_error)


def mcp_tools(url, allowed):
    """OpenAI tool specs for the allowed MCP tools, in the server's own words."""
    try:
        tools = asyncio.run(_list_tools(url))
    except Exception as error:  # network and protocol errors both mean "server not reachable"
        raise AgentError(f"MCP server at {url} is not reachable: {type(error).__name__}") from error
    specs = [tool_spec(tool) for tool in tools if tool.name in allowed]
    missing = set(allowed) - {spec["function"]["name"] for spec in specs}
    if missing:
        raise AgentError(f"MCP server lacks tools: {', '.join(sorted(missing))}")
    return specs


def tool_spec(tool):
    return {"type": "function", "function": {
        "name": tool.name,
        "description": tool.description or "",
        "parameters": tool.input_schema or {"type": "object", "properties": {}},
    }}


def mcp_caller(url):
    def call(name, arguments):
        try:
            return asyncio.run(_call_tool(url, name, arguments))
        except Exception as error:
            raise AgentError(f"Tool {name} failed: {type(error).__name__}") from error
    return call


def parse_json(text):
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None


def run_turn(history, question, chat, call_tool, tools, *, max_steps=MAX_STEPS):
    """Ask one question. Returns (new messages for the history, tool steps, answer text).

    history holds earlier user/assistant/tool messages; the system prompt is added here.
    Only tools named in `tools` may run; any other name the model invents is refused.
    """
    allowed = {spec["function"]["name"] for spec in tools}
    new = [{"role": "user", "content": question}]
    steps = []
    for _ in range(max_steps):
        reply = chat([{"role": "system", "content": SYSTEM_PROMPT}, *history, *new], tools)
        calls = reply.get("tool_calls") or []
        message = {"role": "assistant", "content": reply.get("content") or ""}
        if calls:
            message["tool_calls"] = calls
        new.append(message)
        if not calls:
            return new, steps, message["content"]
        for call in calls:
            function = call.get("function") or {}
            name = function.get("name", "")
            arguments = parse_json(function.get("arguments") or "{}")
            step = {"name": name, "arguments": arguments, "result": None, "error": None}
            if name not in allowed:
                step["error"] = f"Tool {name!r} is not available here"
                text = json.dumps({"error": step["error"]})
            elif not isinstance(arguments, dict):
                step["error"] = "Tool arguments were not a JSON object"
                text = json.dumps({"error": step["error"]})
            else:
                try:
                    text, is_error = call_tool(name, arguments)
                except AgentError as error:
                    text, is_error = json.dumps({"error": str(error)}), True
                step["result"] = parse_json(text)
                step["raw"] = text
                if is_error:
                    step["error"] = text
            steps.append(step)
            new.append({"role": "tool", "tool_call_id": call.get("id", name), "content": text})
    raise AgentError(f"No final answer after {max_steps} model steps")


def report_links(steps):
    """Latest approval link, report id and download links seen in report tool results."""
    found = {}
    for step in steps:
        result = step.get("result")
        if not isinstance(result, dict):
            continue
        for key in ("report_id", "approval_url", "status", "summary"):
            if key in result:
                found[key] = result[key]
        if isinstance(result.get("downloads"), dict):
            found["downloads"] = result["downloads"]
    return found


def review_status(call_tool, review_id):
    """Read-only lookup of one final review; returns (parsed result, raw text)."""
    text, is_error = call_tool(REVIEW_TOOL, {"review_id": review_id.strip()})
    result = parse_json(text)
    if is_error or not isinstance(result, dict):
        raise AgentError(text or "The review service gave no answer")
    return result, text


def download(url, *, timeout=60):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.hostname not in ("127.0.0.1", "localhost"):
        raise AgentError("Downloads come only from the local report server")
    with urlopen(Request(url, headers={"User-Agent": "slmcp-streamlit"}), timeout=timeout) as response:
        return response.read()
