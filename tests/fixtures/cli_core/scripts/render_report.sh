#!/usr/bin/env bash
set -euo pipefail

render_report() {
  printf 'Rendering report to %s\n' "$OUTPUT_PATH"
}

render_report "$@"
