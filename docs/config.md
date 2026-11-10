# Config

One YAML file says where the data comes from and which model to use. Changing the data source means changing the file, not the code.

```yaml
data_source:
  adapter: csv                      # csv | jsonl | rss
  path: examples/posts_sample.csv   # relative to the config file
  # url: [https://example.com/rss]  # rss only: live feeds (path = saved feed files)
  # columns: {created_at: date}     # file column -> schema field
  # defaults: {platform: news}      # values for fields the file lacks
  # assume_tz: Asia/Jakarta         # for dates without a time zone
model:
  endpoint: http://localhost:8000/v1
  name: Qwen3.6-27B
  api_key_env: LLM_API_KEY          # optional: the NAME of an environment variable
language: id
timezone: Asia/Jakarta
```

```python
from slmcp.config import load_config, build_adapter

config = load_config("config.yaml")
adapter = build_adapter(config)       # FileAdapter or RssAdapter
key = config.model.api_key()          # read from the environment, or None
```

## Rules
- **No keys or tokens in the file.** A field whose name contains key, token, password or secret and has a value is refused. Put the key in an environment variable and give its name in `model.api_key_env`.
- **Free, open models only** in examples (e.g. Qwen or Llama served by llama.cpp or Ollama).
- The file is read with `yaml.safe_load` only, so it can't run code.
- Every problem raises `ConfigError` with the field name and how to fix it, e.g. `data_source.adapter: 'excel' is not one of csv, jsonl, rss`.

## Why PyYAML
The standard library has no YAML reader, and the plan asks for YAML (comments, easy to edit by hand). PyYAML is small, widely used, and `safe_load` only builds plain data. It is the project's first runtime dependency. (TOML would need no dependency, via `tomllib`, but it's less familiar for this kind of file.)
