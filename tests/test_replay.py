import json
import shutil
import socket
import subprocess
from pathlib import Path

import pytest
from test_orchestrator import REQUEST, tool_results

from slmcp.analyst import make_brief, reconcile, replay_payload
from slmcp.orchestrator import Run, digests, read_json, write_json
from slmcp.replay import ReplayError, replay_run

REJECTION = Path(__file__).parent / "fixtures/replay/krakatau-tool-rejection.json"


@pytest.fixture
def offline(monkeypatch):
    def network_or_process_call(*args, **kwargs):
        pytest.fail("Replay attempted a network connection or subprocess")

    monkeypatch.setattr(socket.socket, "connect", network_or_process_call)
    monkeypatch.setattr(subprocess, "Popen", network_or_process_call)


def test_replays_recorded_krakatau_tool_rejection(tmp_path, offline):
    shutil.copyfile(REJECTION, tmp_path / "trace.json")
    result = replay_run(tmp_path)
    assert result["recorded_status"] == "failed"
    failure, = result["failures"]
    assert (failure["step"], failure["stage"], failure["tool"]) == (4, "data", "news_recap")
    assert "rejected the request: keyword: too long (7 words)" in failure["message"]
    assert list(tmp_path.iterdir()) == [tmp_path / "trace.json"]


def outline_run(folder):
    run = Run.create(folder, REQUEST)
    recap, trend = tool_results()
    for tool, payload in (("news_recap", recap), ("entity_trend", trend)):
        run.add("data", "tool_call", tool=tool, arguments=REQUEST.tool_args())
        run.add("data", "tool_result", tool=tool, status="ok", payload=replay_payload(tool, payload))
    evidence = reconcile(REQUEST, recap, trend)
    write_json(folder / "evidence.json", evidence)
    run.add("data", "artifact", path="evidence.json", hashes=digests(folder, ("evidence.json",)))
    output = json.dumps({"findings": [{"text": "Ada liputan.", "evidence": ["volume"]}]})
    brief = make_brief(evidence, lambda _: output, "fixture")
    write_json(folder / "analysis.json", brief)
    run.add("analyst", "artifact", path="analysis.json", hashes=digests(folder, ("analysis.json",)))
    outline = folder / "outline"
    outline.mkdir()
    (outline / "outline.md").write_text("Krakatau")
    inputs = {"request": run.trace["request"], "query": {"snapshot": run.trace["snapshot"],
              "timezone": run.trace["timezone"]}, **evidence}
    write_json(outline / "report-inputs.json", inputs)
    files = ("outline/outline.md", "outline/report-inputs.json", "analysis.json")
    run.trace["outline_digests"] = digests(folder, files)
    run.transition("awaiting_outline_approval", artifacts=list(files))
    return folder


def test_reconciles_saved_replies_and_detects_changed_total(tmp_path, offline):
    folder = outline_run(tmp_path / "run")
    observed = replay_run(folder)
    assert observed["data_source"] == "recorded_tool_results"
    assert observed["recorded_status"] == "awaiting_outline_approval"
    assert observed["headline_total"] == reconcile(REQUEST, *tool_results())["facts"]["total"]

    path = folder / "trace.json"
    trace = read_json(path)
    result = next(step for step in trace["steps"] if step["event"] == "tool_result"
                  and step["tool"] == "news_recap")
    result["payload"]["total"] += 1
    write_json(path, trace)
    with pytest.raises(ReplayError, match="headline total") as failure:
        replay_run(folder)
    assert failure.value.location["stage"] == "data"


def test_detects_changed_evidence_artifact_at_its_saved_step(tmp_path, offline):
    folder = outline_run(tmp_path / "run")
    (folder / "evidence.json").write_text("{}")
    with pytest.raises(ReplayError, match="Artifact digest differs for evidence.json") as failure:
        replay_run(folder)
    assert failure.value.location == {"seq": 6, "stage": "data", "event": "artifact"}
