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

## Contributing
- Work on a branch and open a pull request to `main`. Don't push to `main` directly.
- CI must be green: lint (ruff), tests (pytest, Python 3.11 and 3.12), package build, proof run, and the security checks (CodeQL, gitleaks, pip-audit).
- Every pull request has a **Proof** section with real output (see the PR template).
- Update `CHANGELOG.md` with each change.
- The maintainer reviews and merges.

## License
MIT. See [LICENSE](LICENSE).
