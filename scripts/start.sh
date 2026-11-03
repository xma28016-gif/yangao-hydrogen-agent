#!/usr/bin/env bash
set -euo pipefail
PRODUCT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
exec "$PRODUCT_DIR/backend/.venv/bin/python" "$PRODUCT_DIR/scripts/manage.py" start
