# MultiParseRAG development

- This repository is a thin integration layer around MinerU, MinIO and RAGFlow.
- Keep parser, storage, retrieval and API contracts separate. Reuse upstream public APIs.
- Current project version is 0.0.1. Update pyproject.toml and __init__.py together when releasing.
- User-facing copy and README are in Chinese. Use plain, direct statements.
- Never commit .env files, API keys, user documents, model caches or runtime data.
- Use uv.lock and requirements.lock; keep exported Docker requirements synchronized.
- Supported mode is a single API process and one parser worker. Do not enable multiple uvicorn workers.
- Changes to Pydantic API contracts require regenerating docs/openapi.json.
- Before submitting: uv run ruff check .; uv run ruff format --check src tests scripts;
  uv run pytest -q; uv run python scripts/export_schema.py --check.
- Record what was verified with real services separately from contract tests.
- Do not add custom vector databases, authentication platforms or full admin consoles for early releases.
