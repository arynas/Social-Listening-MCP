# social-listening-mcp

**Status: early work, not released.**

Tools that let an LLM agent answer social-listening questions ("How was the news sentiment about X last week?") on **any** data source. All tools talk to one shared post schema, and small adapters connect each data source to that schema. The tools are served over MCP (Model Context Protocol), so any MCP client can use them.

## Quickstart (from zero)
Needs Python 3.11+ and git. The GitHub CLI (`gh`) is optional.

**1. Install**
```bash
git clone https://github.com/arynas/Social-Listening-MCP.git
cd Social-Listening-MCP
python3 -m venv .venv
.venv/bin/pip install -e .
```

**2. Download the data snapshot** (43,422 Indonesian news headlines, 1–14 Oct 2026; see "Data snapshot" below)
```bash
mkdir -p data/snapshot && cd data/snapshot
gh release download data-2026-10-v1 -R arynas/Social-Listening-MCP   # or download the files from the release page
shasum -a 256 -c SHA256SUMS && gunzip -k posts-2026-10.labelled.jsonl.gz
cd ../..
```

**3. Start the server** (stdio; `today` is frozen at 2026-10-15 in this config, because the snapshot ends on 14 Oct)
```bash
.venv/bin/slmcp-server --config config.snapshot.example.yaml
```
It prints `social-listening-mcp 0.0.1 starting (stdio)` and waits for an MCP client. Stop it with Ctrl+C.

**4. Connect an MCP client.** Most clients take a command to start a local server. Give it the full paths:
```json
{"command": ["/path/to/Social-Listening-MCP/.venv/bin/slmcp-server",
             "--config", "/path/to/Social-Listening-MCP/config.snapshot.example.yaml"]}
```
For example, in OpenCode put this under `mcp` in a project `opencode.json` as `{"type": "local", "command": [...]}`. Clients that only speak HTTP can use `--transport streamable-http` (then the URL is `http://127.0.0.1:8000/mcp`). Use any chat model that supports tool calls; our trial used Qwen3.6-27B (open weights) with thinking off (see `docs/client-trial.md`).

**5. Ask.** Example questions:
- Bagaimana sentimen berita tentang banjir dari 1 sampai 14 Oktober 2026?
- Berapa jumlah berita tentang gempa minggu lalu?
- Apa saja sumber berita teratas tentang kesehatan kemarin? Sertakan 3 contoh judul dengan link.

You can also check the tools without a model: `.venv/bin/python scripts/proof.py` calls them through MCP on the sample data.

## Data rules
- **Fresh code.** Everything here is written from scratch. No company code.
- **Public or synthetic data only.** No private or company data, no client names.
- **No keys or secrets in the repo.** Keys go in a local `.env` file, which git ignores.
- **Free models only** in examples and demos.

## Dependencies
- Runtime: **PyYAML** (only `yaml.safe_load`, for the config file) and the official **MCP Python SDK** (`mcp`, 2.x) for the server. Everything else uses the Python standard library.
- Development: pytest, ruff.

## Config
Copy `config.example.yaml` to `config.yaml` and edit it. See `docs/config.md`. Keys never go in the file.

## Layout
- `slmcp/`: the package
- `slmcp/adapters/`: one adapter per data source (CSV/JSON and RSS news are planned first)
- `slmcp/bench/`: benchmark builder, no-model check, model runner and scorer
- `benchmark/`: the benchmark question set (`questions-v1.1.jsonl`; v1 kept for history) and smoke runs
- `tests/`: tests (pytest)
- `docs/`: documentation
- `examples/`: small sample data (synthetic)

## Run the tests
```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/ruff check .
```

## MCP server
Five tools, `news_recap`, `social_recap`, `detect_spikes`, `compare_keywords` and `entity_trend`, over stdio (default) or Streamable HTTP. See `docs/tools.md`, `docs/spikes.md`, `docs/compare.md` and `docs/trend.md`.

```bash
pip install -e .
slmcp-server --config config.snapshot.example.yaml
```

## Benchmark
226 questions (113 in Indonesian, the same in English) on the snapshot, with gold answers computed by code, a no-model check, a runner for any OpenAI-compatible model and an automatic scorer. See `docs/benchmark.md`. Known failure modes, their fixes and the V&V checklist are in `docs/failure-vv.md`; each fixed failure has a regression test (`docs/regressions.md`).

## Data snapshot
A ready-made snapshot of 43,422 Indonesian news headlines (1–14 Oct 2026) with sentiment labels is published as a GitHub Release: [data-2026-10-v1](https://github.com/arynas/Social-Listening-MCP/releases/tag/data-2026-10-v1). See `docs/datasheet.md` for how it was made and its limits (headlines only; the sentiment labels are rough model output).

```bash
mkdir -p data/snapshot && cd data/snapshot
gh release download data-2026-10-v1 -R arynas/Social-Listening-MCP   # or download the files from the release page
shasum -a 256 -c SHA256SUMS && gunzip -k posts-2026-10.labelled.jsonl.gz
```

Load it with a config file (`data_source: {adapter: jsonl, path: data/snapshot/posts-2026-10.labelled.jsonl}`) or directly:

```python
from slmcp.adapters.csv_json import FileAdapter

adapter = FileAdapter("data/snapshot/posts-2026-10.labelled.jsonl")
print(len(adapter.posts))  # 43422
hits = adapter.search("banjir", "2026-10-01", "2026-10-14")
```

A longer snapshot, 1 Sep – 14 Oct 2026 (144,722 headlines; the October file is the same as in v1), is release [data-2026-10-v2](https://github.com/arynas/Social-Listening-MCP/releases/tag/data-2026-10-v2). Benchmark v2 and `config.snapshot-s1.example.yaml` use it:

```bash
mkdir -p data/snapshot_s1/out && cd data/snapshot_s1/out
gh release download data-2026-10-v2 -R arynas/Social-Listening-MCP
shasum -a 256 -c SHA256SUMS && gunzip -k posts-2026-09.labelled.jsonl.gz posts-2026-10.labelled.jsonl.gz
```

**Citation (required when you use or share the data):** Data from the GDELT Project, https://www.gdeltproject.org/

## Prepare report facts and an outline

From the repository root, run the local command against a configured news adapter. It needs no model.

```bash
python -m slmcp.report --config config.snapshot-s1.example.yaml \
  --keyword krakatau --start 2026-09-01 --end 2026-09-14 \
  --snapshot data-2026-10-v2 > report-inputs.json
```

The JSON contains the query and snapshot label, daily counts, peak, previous-period change when available,
example headline links, warnings, fact sources, and an outline that references those facts. A covered range
with no matches has a total of zero. An out-of-coverage range has empty facts and a coverage warning.

### Serve checked reports over MCP

Reports are an optional Streamable HTTP feature. Point to a local checked renderer with the
`build-report --report-inputs ... --output-dir ...` interface. It must write `report.pptx`, `report.pdf`,
and `check_report.json` with `status: pass` for successful builds.

```bash
slmcp-server --config config.snapshot-s1.example.yaml --transport streamable-http --port 8769 \
  --report-renderer /path/to/renderer/generate.py --report-python /path/to/renderer/python3 \
  --report-dir data/report-jobs --report-snapshot data-2026-10-v2 \
  --report-source-name 'GDELT Project' --report-source-url https://www.gdeltproject.org/
```

1. Call `build_report` with a keyword, `dates: {start, end}`, `pages: 4`, and `template: "news-pilot"`.
   Keep the returned report ID and show the approval link and outline to the user.
2. Open the approval link and approve the outline in the browser. The page returns immediately while the
   checked renderer runs. The same approval queues one job. A failed job has a separate retry action.
3. Call `report_status` with the report ID. The response includes PowerPoint and PDF links after verification
   passes. An uncovered period produces a one-slide coverage report after approval.

The example listens on loopback. For a chat service on another machine, use its existing authenticated
HTTPS ingress, route `/mcp` and `/reports/` to the same server, and set `--report-base-url` to the browser-facing
origin. Configure the chat backend to call that MCP endpoint. Approval and download links grant access to
their holder, so keep them within the intended chat and out of shared access logs. Run one server process
per report directory. The state and completed files remain in that directory across restarts.

### Run the Analyst Agent pilot

The local S3 pilot reads MCP news evidence, drafts a brief with evidence references, and prepares the S2
report outline. After outline approval it builds a checked draft for final human review. [Pilot setup and commands](docs/orchestrator.md)

## Contributing
- Work on a branch and open a pull request to `main`. Don't push to `main` directly.
- CI must be green: lint (ruff), tests (pytest, Python 3.11 and 3.12), package build, proof run, and the security checks (CodeQL, gitleaks, pip-audit).
- Every pull request has a **Proof** section with real output (see the PR template).
- Update `CHANGELOG.md` with each change.
- The maintainer reviews and merges.

## License
MIT. See [LICENSE](LICENSE).
