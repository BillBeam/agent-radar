#!/usr/bin/env bash
# Generate the real launchd plists from deploy/*.plist (filling this repo's absolute path)
# and load them — the unattended daily + the voting-listener常驻 in one command.
#
#   bash scripts/install-launchd.sh [daily|serve|both]   # default: both
#   bash scripts/install-launchd.sh uninstall            # unload + remove
#
# The generated plists live in ~/Library/LaunchAgents (outside the repo → never committed).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LA="$HOME/Library/LaunchAgents"; mkdir -p "$LA"
WHAT="${1:-both}"

_one() {
  local name="$1" dst="$LA/com.agentradar.$1.plist"
  sed "s|__REPO__|$REPO|g" "$REPO/deploy/com.agentradar.$name.plist" > "$dst"
  launchctl unload "$dst" 2>/dev/null || true
  launchctl load "$dst"
  echo "  loaded com.agentradar.$name → $dst"
}
_rm() {
  local dst="$LA/com.agentradar.$1.plist"
  launchctl unload "$dst" 2>/dev/null || true
  rm -f "$dst" && echo "  removed com.agentradar.$1"
}

case "$WHAT" in
  uninstall) _rm daily; _rm serve ;;
  daily)     _one daily ;;
  serve)     _one serve ;;
  both)      _one daily; _one serve ;;
  *) echo "usage: $0 [daily|serve|both|uninstall]"; exit 1 ;;
esac
echo "done. verify: launchctl list | grep agentradar    (logs: data/state/launchd-*.log)"
