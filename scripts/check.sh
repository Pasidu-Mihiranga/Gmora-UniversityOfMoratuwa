#!/usr/bin/env bash
# Runs exactly what CI runs. Every contributor and every coding model runs this before a pull request.
# Usage: scripts/check.sh            (Postgres must be up: cd project && docker compose up -d db)
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== text style (no em or en dashes)"
python3 scripts/check_text.py

cd project
PY=.venv/bin/python
[ -x "$PY" ] || PY=python

echo "== lint and format"
$PY -m ruff check suwapath tests migrations main.py
$PY -m ruff format --check suwapath tests migrations main.py

echo "== module boundaries"
$PY -m importlinter.cli lint 2>/dev/null || .venv/bin/lint-imports

echo "== types (contracts, platform, bootstrap)"
$PY -m mypy

echo "== migrations"
$PY -m alembic upgrade head
$PY -m alembic check

echo "== tests"
$PY -m pytest -q -o addopts=""

if [ -f frontend/package.json ]; then
  echo "== frontend"
  (cd frontend && npx tsc -b && npm run lint && npm test && npm run build)
fi
echo "All checks passed."
