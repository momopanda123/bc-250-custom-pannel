#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
APPLICATION_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_FILE="$APPLICATION_DIR/bc250-custom-pannel.desktop"

mkdir -p "$APPLICATION_DIR"
desktop_tmp="$(mktemp "$APPLICATION_DIR/.bc250-custom-pannel.XXXXXX")"
trap 'rm -f "$desktop_tmp"' EXIT

{
    printf '%s\n' '[Desktop Entry]'
    printf '%s\n' 'Type=Application'
    printf '%s\n' 'Name=BC-250 Control Center'
    printf '%s\n' 'Name[ko]=BC-250 제어 센터'
    printf '%s\n' 'Comment=BC-250 GPU status and safe controls'
    printf '%s\n' 'Comment[ko]=BC-250 GPU 상태와 안전 제어'
    printf 'Exec="%s/run.sh"\n' "$SCRIPT_DIR"
    printf 'TryExec=%s/run.sh\n' "$SCRIPT_DIR"
    printf '%s\n' 'Icon=video-display'
    printf '%s\n' 'Terminal=false'
    printf '%s\n' 'Categories=System;Monitor;'
    printf '%s\n' 'Keywords=BC-250;GPU;CU;Bazzite;Governor;'
    printf '%s\n' 'StartupNotify=true'
} >"$desktop_tmp"

chmod 0644 "$desktop_tmp"
mv -f "$desktop_tmp" "$DESKTOP_FILE"
trap - EXIT

if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$DESKTOP_FILE"
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPLICATION_DIR"
fi

printf '앱 목록에 등록했습니다: %s\n' "$DESKTOP_FILE"
