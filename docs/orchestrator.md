# Analyst Agent pilot

The S3-1 pilot runs the data, analyst, report and reviewer stages against the public `data-2026-10-v2` snapshot. Start it from the command line and review the outline before authorizing rendering.

## Setup

Install Social Listening MCP and the S2 `llm-report-kit` in their own Python environments. Set `KIT` to the report kit executable. The kit's README lists the LibreOffice requirement. Configure an OpenAI-compatible text endpoint with `ANALYST_BASE_URL`, `ANALYST_MODEL`, and an optional `ANALYST_API_KEY` environment variable. Use `--thinking-off` with a backend that supports the Qwen chat-template option.

From the Social Listening MCP repository root, download the public snapshot and start a local MCP server:

```sh
DATA=data/snapshot_s1/out
"$KIT" fetch-data --data-dir "$DATA"
slmcp-server --config config.snapshot-s1.example.yaml --transport streamable-http \
  --host 127.0.0.1 --port 8769
```

## Run the two cases

In a second terminal with the same environment and working directory:

```sh
DATA=data/snapshot_s1/out
python -m slmcp.orchestrator start --run runs/s3-krakatau \
  --keyword Krakatau --start 2026-09-01 --end 2026-09-14 \
  --kit "$KIT" --data-dir "$DATA" --analyst-base "$ANALYST_BASE_URL" \
  --analyst-model "$ANALYST_MODEL" --analyst-key-env ANALYST_API_KEY
cat runs/s3-krakatau/outline/outline.md
cat runs/s3-krakatau/analysis.json
python -m slmcp.orchestrator approve-outline --run runs/s3-krakatau \
  --kit "$KIT" --data-dir "$DATA"
```

Use a new run directory and the dates `2026-08-18` through `2026-08-31` for the out-of-coverage case. The analyst supplies a coverage message for that case without calling the model. The `start` command accepts `--mcp-url` for a different server URL. Omit `--analyst-key-env` if the endpoint accepts unauthenticated requests.

## Run files and state

- `evidence.json` contains the reconciled MCP facts and warnings. Both tool calls are recorded in `trace.json`.
- `analysis.json` contains draft findings with references to the evidence sections. Figures in the report come from the data, and the brief accompanies the S2 PowerPoint and PDF in the report bundle.
- `outline/` contains the S2 outline and structured inputs. The orchestrator compares the outline facts with MCP evidence before waiting for approval. The approval applies to the recorded hashes of the outline, its inputs, and the analysis brief.
- `report/` contains the local reviewed draft and S2 check report. The reviewer confirms the checked figures and artefacts against the approved inputs. A successful run stops at `awaiting_final_approval`.

`trace.json` keeps the run ID, ordered stages, durations, decisions, and the failed stage if any. Resume the prepared run with `approve-outline`. Start a fresh run for a changed outline or a run interrupted during rendering. Use the rubric and report tool constraints from the S2 pilot, including dates with a full daily series.
