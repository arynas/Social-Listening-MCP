# Analyst Agent pilot

The local pilot runs the data, analyst, report and reviewer stages against the public `data-2026-10-v2` snapshot. Start it from the command line and review the outline before authorizing rendering.

## Setup

Install Social Listening MCP and `llm-report-kit` 0.1.1 or later in their own Python environments. Set `KIT` to the report kit executable. The kit's README lists the LibreOffice requirement. Configure OpenAI-compatible endpoints using `ANALYST_BASE_URL`, `ANALYST_MODEL`, `REVIEWER_BASE_URL`, and `REVIEWER_MODEL`. For authenticated endpoints, set `ANALYST_API_KEY` and `REVIEWER_API_KEY`. Use `--thinking-off` with a backend that supports the Qwen chat-template option.

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
  --kit "$KIT" --data-dir "$DATA" --reviewer-base "$REVIEWER_BASE_URL" \
  --reviewer-model "$REVIEWER_MODEL" --reviewer-key-env REVIEWER_API_KEY
```

Use a new run directory and the dates `2026-08-18` through `2026-08-31` for the out-of-coverage case. That case uses the coverage message and local checks, so `approve-outline` can run without a reviewer endpoint. The `start` command accepts `--mcp-url` for a different server URL. Omit a key argument if its endpoint accepts unauthenticated requests.

## Run files and state

- `evidence.json` contains the reconciled MCP facts and warnings. Both tool calls are recorded in `trace.json`.
- `analysis.json` contains draft findings with references to the evidence sections. Figures in the report come from the data, and the brief accompanies the S2 PowerPoint and PDF in the report bundle.
- `outline/` contains the S2 outline and structured inputs. The orchestrator compares the outline facts with MCP evidence before waiting for approval. The approval applies to the recorded hashes of the outline, its inputs, and the analysis brief.
- `drafts/` keeps every report candidate and its `review.json`. The reviewer reruns the S2 checks against the approved facts and checks whether the brief's conclusions follow from their cited evidence. Findings identify a file or claim, its evidence and the worker responsible for correction.
- `report/` contains the passing draft, including both check reports. The orchestrator rebuilds a report with figure or link findings from approved inputs. It sends analysis findings back for a revised brief and rechecks the next draft. After two unsuccessful revisions the trace records `review_escalated` and retains the drafts. A passing run stops at `awaiting_final_approval`.

`trace.json` keeps the run ID, ordered stages, durations, decisions, and the failed stage if any. Resume the prepared run with `approve-outline`. Start a fresh run for a changed outline or a run interrupted during rendering. Use the rubric and report tool constraints from the S2 pilot, including dates with a full daily series.

## Reproduce the reviewer test

`scripts/prove_s3_review.py` runs the clean Krakatau brief and three seeded defects against live MCP, the installed S2 checker and the configured reviewer. Run it with the kit's Python interpreter for the slide utilities, with `PYTHONPATH=.` from this repository. Set the reviewer environment variables above. Pass the clean S3-1 `analysis.json` with `--pilot-brief`, the verified snapshot folder with `--data-dir`, the kit executable with `--kit`, and a new output folder with `--out`. Each case keeps the initial draft, findings, revisions, trace and final report.

## Final report approval in Open WebUI

A reviewed run can be published to the pilot final review service. It verifies the hashes in the run trace, copies the checked bundle into SQLite, and assigns a case ID and version. The browser page shows the brief, reviewer results, and the exact PDF and PPTX for that version.

```sh
umask 077
DB=runs/final-reviews.sqlite3
python -m slmcp.final_review publish --db "$DB" --run runs/s3-krakatau
python -m slmcp.final_review serve --db "$DB" --port 8771 --browser-url http://127.0.0.1:8771
```

In Open WebUI, add an MCP Streamable HTTP connection at `http://127.0.0.1:8771/mcp`. The Open WebUI backend must be able to reach that address and the browser must be able to reach the configured browser URL. Enable the `get_analyst_final_review` tool for the pilot model with native tool calling. Ask the model to look up the case ID returned by `publish`, then follow the link it returns. The tool looks up status. The page accepts **Approve report**, **Request changes**, or **Reject report**. Keep the pilot on loopback or a restricted tunnel. Its case IDs and review links grant access by possession, without a separate account on the review page. Share them only with intended reviewers.

Approval records one simulated delivery receipt containing the approved version, bundle digest, and download hashes. Rejection closes the case. A change request requires a note and prevents approval until the operator prepares a replacement.

To revise, copy the case ID to `REVIEW_ID`. Use a new run folder. The analyst receives the stored change note, and the replacement goes through the same outline approval, S2 checks, and reviewer.

```sh
python -m slmcp.final_review start-revision --db "$DB" --review-id "$REVIEW_ID" \
  --run runs/s3-krakatau-v2 --data-dir "$DATA" --kit "$KIT" \
  --analyst-base "$ANALYST_BASE_URL" --analyst-model "$ANALYST_MODEL" \
  --analyst-key-env ANALYST_API_KEY
cat runs/s3-krakatau-v2/outline/outline.md
cat runs/s3-krakatau-v2/analysis.json
python -m slmcp.orchestrator approve-outline --run runs/s3-krakatau-v2 \
  --kit "$KIT" --data-dir "$DATA" --reviewer-base "$REVIEWER_BASE_URL" \
  --reviewer-model "$REVIEWER_MODEL" --reviewer-key-env REVIEWER_API_KEY
python -m slmcp.final_review publish --db "$DB" --review-id "$REVIEW_ID" --run runs/s3-krakatau-v2
python -m slmcp.final_review status --db "$DB" --review-id "$REVIEW_ID"
```

Look up the same case ID in Open WebUI to open the current version. Old review pages keep their original files and show the version as superseded. An approval submitted from an old page returns HTTP 409. The SQLite store retains version manifests, decision events, and the simulated delivery receipt.
