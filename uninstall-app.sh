#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
APPLICATION_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_FILE="$APPLICATION_DIR/bc250-custom-pannel.desktop"

rm -f "$DESKTOP_FILE"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPLICATION_DIR"
fi

printf '앱 목록 등록을 제거했습니다. 프로젝트는 그대로 유지됩니다: %s\n' "$SCRIPT_DIR"
