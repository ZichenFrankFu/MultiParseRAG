"""Generate the checked-in API contract without requiring upstream services."""

import argparse
import json
import tempfile
from pathlib import Path

from multiparserag.config import Settings
from multiparserag.main import create_app

parser = argparse.ArgumentParser()
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
with tempfile.TemporaryDirectory() as directory:
    app = create_app(Settings(_env_file=None, data_dir=Path(directory), storage_backend="local"))
    content = json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"
path = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"
if args.check:
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        raise SystemExit("OpenAPI changed. Run: uv run python scripts/export_schema.py")
else:
    path.write_text(content, encoding="utf-8")
    print(path)
