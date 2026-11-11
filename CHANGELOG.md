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
- CI (lint, tests on Python 3.11 and 3.12, package build, proof run) and security checks (CodeQL, gitleaks, pip-audit).
