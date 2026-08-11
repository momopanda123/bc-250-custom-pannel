#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' '오류: Bazzite의 Python 3를 찾지 못했습니다.' >&2
    exit 1
fi

if [[ $# -eq 0 && -t 0 && -t 1 ]]; then
    STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/bc250-custom-pannel"
    mkdir -p "$STATE_DIR"
    if command -v setsid >/dev/null 2>&1; then
        setsid -f python3 "$SCRIPT_DIR/app.py" </dev/null >>"$STATE_DIR/app.log" 2>&1
    else
        nohup python3 "$SCRIPT_DIR/app.py" </dev/null >>"$STATE_DIR/app.log" 2>&1 &
    fi
    exit 0
fi

exec python3 "$SCRIPT_DIR/app.py" "$@"
