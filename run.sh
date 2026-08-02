#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' '오류: Bazzite의 Python 3를 찾지 못했습니다.' >&2
    exit 1
fi

exec python3 "$SCRIPT_DIR/app.py" "$@"
