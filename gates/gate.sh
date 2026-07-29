#!/bin/bash
# Gate harness. Performs the exact reset sequence SPEC-V3 describes, then launches
# Chrome against the disposable copy.
#
#   ./gates/gate.sh template   -> open the template profile for setup/maintenance
#   ./gates/gate.sh run [url]  -> copy template -> runtime, launch runtime
#
# This is a throwaway test harness, not part of the scraper.

set -euo pipefail

ROOT="/Users/sor/Dev/youtube/research/pipelines/extraction/tools/scraper"
CHROME="/Users/sor/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
TEMPLATE="$ROOT/gates/data-dir-template"
RUNTIME="$ROOT/gates/data-dir"
PROBE="$ROOT/gates/probe-extension"

COMMON_FLAGS=(
  --profile-directory=Default
  --no-first-run
  --no-default-browser-check
  --autoplay-policy=document-user-activation-required
  --mute-audio
)

in_use() {
  local dir="$1"
  [ -d "$dir" ] || return 1
  lsof +D "$dir" >/dev/null 2>&1
}

case "${1:-}" in
  template)
    if in_use "$TEMPLATE"; then
      echo "ERROR: a Chrome process is using $TEMPLATE. Close it first." >&2
      exit 1
    fi
    mkdir -p "$TEMPLATE"
    echo "Opening TEMPLATE profile: $TEMPLATE"
    echo "Load the probe unpacked from: $PROBE"
    exec "$CHROME" --user-data-dir="$TEMPLATE" "${COMMON_FLAGS[@]}" \
      "chrome://extensions"
    ;;

  run)
    URL="${2:-about:blank}"

    if [ ! -d "$TEMPLATE" ]; then
      echo "ERROR: no template at $TEMPLATE. Run: ./gates/gate.sh template" >&2
      exit 1
    fi
    for d in "$TEMPLATE" "$RUNTIME"; do
      if in_use "$d"; then
        echo "ERROR: a Chrome process is using $d. Close it first." >&2
        exit 1
      fi
    done

    echo "==> removing old runtime"
    rm -rf "$RUNTIME"

    echo "==> copying template -> runtime"
    time /usr/bin/ditto "$TEMPLATE" "$RUNTIME"

    echo "==> clearing Singleton* locks from the copy"
    find "$RUNTIME" -maxdepth 2 -name 'Singleton*' -print -delete || true

    echo "==> launching runtime profile"
    echo "    url: $URL"
    exec "$CHROME" --user-data-dir="$RUNTIME" "${COMMON_FLAGS[@]}" \
      --new-window "$URL"
    ;;

  *)
    echo "usage: $0 {template|run [url]}" >&2
    exit 2
    ;;
esac
