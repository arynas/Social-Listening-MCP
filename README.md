# social-listening-mcp

**Status: early work, not released.**

Tools that let an LLM agent answer social-listening questions ("How was the news sentiment about X last week?") on **any** data source. All tools talk to one shared post schema, and small adapters connect each data source to that schema. The tools will be served over MCP (Model Context Protocol).

## Data rules
- **Fresh code.** Everything here is written from scratch. No company code.
- **Public or synthetic data only.** No private or company data, no client names.
- **No keys or secrets in the repo.** Keys go in a local `.env` file, which git ignores.
- **Free models only** in examples and demos.

## Dependencies
- Runtime: **PyYAML** (only `yaml.safe_load`, for the config file). Everything else uses the Python standard library.
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
