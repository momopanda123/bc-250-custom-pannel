#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

python3 -m unittest discover -s tests -v
bash -n run.sh install-app.sh uninstall-app.sh tests/run-tests.sh
python3 -m compileall -q app.py bc250 bc250_install.py bc250_privileged.py
