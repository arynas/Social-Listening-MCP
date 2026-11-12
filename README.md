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
Two tools, `news_recap` and `social_recap`, over stdio (default) or Streamable HTTP. See `docs/tools.md`.

```bash
pip install -e .
slmcp-server --config config.snapshot.example.yaml
```

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

**Citation (required when you use or share the data):** Data from the GDELT Project, https://www.gdeltproject.org/

## Contributing
- Work on a branch and open a pull request to `main`. Don't push to `main` directly.
- CI must be green: lint (ruff), tests (pytest, Python 3.11 and 3.12), package build, proof run, and the security checks (CodeQL, gitleaks, pip-audit).
- Every pull request has a **Proof** section with real output (see the PR template).
- Update `CHANGELOG.md` with each change.
- The maintainer reviews and merges.

## License
MIT. See [LICENSE](LICENSE).
