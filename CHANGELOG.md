# Changelog

## 0.0.1 (unreleased)
Foundation for data-agnostic social-listening tools (F0–F5).
- Post schema with validation (`slmcp/schema.py`).
- Adapter interface with one shared query rule set and a reusable contract test suite (`slmcp/adapters/base.py`).
- Adapters: in-memory, CSV/JSONL files, RSS/Atom news feeds.
- Synthetic data generator with a fixed seed (`scripts/make_synthetic.py`).
- Snapshot builders for public GDELT news metadata: DOC API and raw translingual GKG files.
- Sentiment labelling with an OpenAI-style endpoint and a blind hand-check tool.
- YAML config (`config.example.yaml`): switch the data source without code changes; keys only from environment variables.
- Datasheet: sentiment labels for the 1–14 Oct 2026 snapshot (43,422 headlines; model-assigned; blind hand check of 100 rows: 63% agreement, kappa 0.44).
- Data snapshot published as GitHub Release `data-2026-10-v1` (labelled headlines, manifest, datasheet, checksums); README shows how to download and load it.
- Recap logic (`slmcp/recap.py`): totals, sentiment with percentages that add up to 100, trend per WIB day or week, top lists, samples, relative dates, data warnings, a ~2,000-token size limit; `news_recap` / `social_recap` as plain functions. Config: `today` and `data_source.sentiment_labels`; `config.snapshot.example.yaml`.
- MCP server (`slmcp/server.py`, `slmcp-server` command) with `news_recap` and `social_recap`, built on the official MCP Python SDK (`mcp>=2.3,<3`); stdio and Streamable HTTP; keyword and date checks with clear errors; one JSON log line per call; proof run calls the tools through MCP.
- Client trial with OpenCode and Qwen3.6-27B on 5 questions (`docs/client-trial.md`); README Quickstart from zero.
- CI (lint, tests on Python 3.11 and 3.12, package build, proof run) and security checks (CodeQL, gitleaks, pip-audit).
- Failure table and V&V checklist for the public version (`docs/failure-vv.md`), numbered like the regression tests.
- `detect_spikes` tool (S1-1): spikes in daily or hourly counts against a trailing robust baseline (median + MAD), separate negative-news spikes, settings in `spikes:` in the config (`docs/spikes.md`). Benchmark runner `--tools` (default: the two v1.1 tools, so v1.1 results stay comparable).
