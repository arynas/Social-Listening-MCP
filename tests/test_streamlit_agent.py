"""Tool loop of the Streamlit demo, with a fake model and fake MCP tools (no network)."""
import io
import json

import pytest
from mcp.types import Tool

from apps.streamlit import agent

RECAP = {"keyword": "banjir", "total": 248, "data_available": True,
         "sentiment_total": {"negative": 138, "positive": 89, "neutral": 21}}
TOOLS = [{"type": "function", "function": {"name": "news_recap", "description": "", "parameters": {}}}]


def call(name, arguments, call_id="c1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


def scripted(*replies):
    seen = []

    def chat(messages, tools):
        seen.append((messages, tools))
        return replies[len(seen) - 1]

    return chat, seen


def test_tool_result_reaches_the_model_unchanged_and_steps_are_recorded():
    args = {"keyword": "banjir", "start_date": "2026-10-01", "end_date": "2026-10-14"}
    chat, seen = scripted({"content": None, "tool_calls": [call("news_recap", args)]},
                          {"content": "Ada 248 berita."})
    calls = []

    def tool(name, arguments):
        calls.append((name, arguments))
        return json.dumps(RECAP), False

    new, steps, answer = agent.run_turn([], "Berapa berita banjir?", chat, tool, TOOLS)
    assert calls == [("news_recap", args)]
    assert answer == "Ada 248 berita."
    assert steps == [{"name": "news_recap", "arguments": args, "result": RECAP,
                      "raw": json.dumps(RECAP), "error": None}]
    tool_message = seen[1][0][-1]
    assert tool_message == {"role": "tool", "tool_call_id": "c1", "content": json.dumps(RECAP)}
    assert seen[0][0][0]["role"] == "system" and seen[0][1] == TOOLS
    assert [m["role"] for m in new] == ["user", "assistant", "tool", "assistant"]


def test_history_is_sent_before_the_new_question():
    chat, seen = scripted({"content": "Halo"})
    history = [{"role": "user", "content": "lama"}, {"role": "assistant", "content": "jawab"}]
    agent.run_turn(history, "baru", chat, lambda *a: ("", False), TOOLS)
    assert [m["content"] for m in seen[0][0][1:]] == ["lama", "jawab", "baru"]


def test_unknown_tool_and_bad_arguments_are_refused_without_calling_mcp():
    bad_args = {"id": "c2", "function": {"name": "news_recap", "arguments": "not json"}}
    chat, seen = scripted({"content": "", "tool_calls": [call("delete_all", {}), bad_args]},
                          {"content": "Maaf."})

    def tool(*_):
        raise AssertionError("must not be called")

    _, steps, answer = agent.run_turn([], "q", chat, tool, TOOLS)
    assert answer == "Maaf."
    assert "not available" in steps[0]["error"]
    assert steps[1]["error"] == "Tool arguments were not a JSON object"
    assert "not available" in json.loads(seen[1][0][-2]["content"])["error"]


def test_tool_failure_is_passed_to_the_model_as_an_error():
    chat, seen = scripted({"content": "", "tool_calls": [call("news_recap", {"keyword": "x"})]},
                          {"content": "Tool gagal."})

    def tool(name, arguments):
        raise agent.AgentError("Tool news_recap failed: ConnectError")

    _, steps, _ = agent.run_turn([], "q", chat, tool, TOOLS)
    assert "ConnectError" in steps[0]["error"]
    assert "ConnectError" in seen[1][0][-1]["content"]


def test_loop_stops_after_max_steps():
    looping = {"content": "", "tool_calls": [call("news_recap", {"keyword": "x"})]}
    with pytest.raises(agent.AgentError, match="No final answer after 2"):
        agent.run_turn([], "q", lambda *_: looping, lambda *_: ("{}", False), TOOLS, max_steps=2)


def test_report_links_keep_the_latest_status_and_downloads():
    steps = [
        {"result": {"report_id": "r1", "status": "awaiting_approval", "approval_url": "http://a",
                    "summary": {"total": 1976, "peak": {"at": "2026-09-06", "count": 733}}}},
        {"result": None},
        {"result": {"report_id": "r1", "status": "ready",
                    "downloads": {"pptx": "http://127.0.0.1:8769/reports/r1/report.pptx"}}},
    ]
    links = agent.report_links(steps)
    assert links["status"] == "ready" and links["approval_url"] == "http://a"
    assert links["summary"]["total"] == 1976 and "pptx" in links["downloads"]


def test_review_status_is_read_only_and_reports_unknown_ids():
    calls = []

    def tool(name, arguments):
        calls.append((name, arguments))
        if arguments["review_id"] == "missing":
            return "Final report review not found. Ask the analyst for the case ID.", False
        return json.dumps({"state": "approved", "version": 2}), False

    result, _ = agent.review_status(tool, " e4753a09 ")
    assert result == {"state": "approved", "version": 2}
    assert calls[0] == ("get_analyst_final_review", {"review_id": "e4753a09"})
    with pytest.raises(agent.AgentError, match="not found"):
        agent.review_status(tool, "missing")


def test_settings_add_torcons_only_when_its_endpoint_is_set():
    plain = agent.load_settings({})
    assert [p.label for p in plain.profiles] == ["Qwen3.6-27B"]
    assert plain.mcp_url == "http://127.0.0.1:8769/mcp"
    both = agent.load_settings({"TORCONS_BASE_URL": "https://example.test/v1", "MODEL_NAME": "Torcons-Pax"})
    torcons = both.profiles[1]
    assert (torcons.model, torcons.key_env, torcons.thinking_off) == ("Torcons-Pax", "TORCONS_API_KEY", False)


def test_openai_chat_sends_thinking_flag_and_key_only_when_configured(monkeypatch):
    sent = []

    def fake_urlopen(request, timeout):
        sent.append((request.full_url, dict(request.header_items()), json.loads(request.data)))
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode())

    monkeypatch.setattr(agent, "urlopen", fake_urlopen)
    monkeypatch.setenv("FAKE_KEY", "secret")
    qwen = agent.ModelProfile("Q", "http://127.0.0.1:8000/v1/", "q")
    hosted = agent.ModelProfile("T", "https://example.test/v1", "t", key_env="FAKE_KEY", thinking_off=False)
    assert agent.openai_chat(qwen)([{"role": "user", "content": "x"}], TOOLS) == {"content": "ok"}
    agent.openai_chat(hosted)([], [])
    (url, headers, payload), (_, hosted_headers, hosted_payload) = sent
    assert url == "http://127.0.0.1:8000/v1/chat/completions"
    assert payload["chat_template_kwargs"] == {"enable_thinking": False} and payload["temperature"] == 0
    assert payload["tools"] == TOOLS and "Authorization" not in headers
    assert hosted_headers["Authorization"] == "Bearer secret"
    assert "chat_template_kwargs" not in hosted_payload and "tools" not in hosted_payload


def test_openai_chat_rejects_inline_credentials():
    with pytest.raises(agent.AgentError, match="without inline credentials"):
        agent.openai_chat(agent.ModelProfile("x", "http://user:pw@host/v1", "m"))


def test_tool_spec_uses_the_server_description_and_schema():
    tool = Tool(name="news_recap", description="Count headlines.",
                input_schema={"type": "object", "properties": {"keyword": {"type": "string"}}})
    spec = agent.tool_spec(tool)
    assert spec["function"]["description"] == "Count headlines."
    assert spec["function"]["parameters"]["properties"] == {"keyword": {"type": "string"}}


def test_downloads_only_come_from_the_local_report_server():
    with pytest.raises(agent.AgentError, match="local report server"):
        agent.download("https://example.test/report.pdf")


def test_refused_request_shows_the_status_and_the_endpoint_message(monkeypatch):
    from urllib.error import HTTPError

    def refuse(request, timeout):
        body = io.BytesIO(b'{"error":{"message":"Invalid API Key","code":401}}')
        raise HTTPError(request.full_url, 401, "Unauthorized", {}, body)

    monkeypatch.setattr(agent, "urlopen", refuse)
    with pytest.raises(agent.AgentError, match=r"HTTP 401 \(Invalid API Key\)"):
        agent.openai_chat(agent.ModelProfile("Q", "http://127.0.0.1:8000/v1", "q"))([], [])


def test_account_ids_in_gateway_messages_are_masked(monkeypatch):
    from urllib.error import HTTPError

    def limit(request, timeout):
        message = "Rate limit exceeded for user: 12345678-aaaa-4bbb-8ccc-1234567890ab-monthly. Limit"
        body = io.BytesIO(json.dumps({"error": {"message": message}}).encode())
        raise HTTPError(request.full_url, 429, "Too Many Requests", {}, body)

    monkeypatch.setattr(agent, "urlopen", limit)
    with pytest.raises(agent.AgentError) as caught:
        agent.openai_chat(agent.ModelProfile("T", "https://example.test/v1", "t"))([], [])
    assert "HTTP 429" in str(caught.value) and "<account>" in str(caught.value)
    assert "12345678-aaaa" not in str(caught.value)
