#!/bin/sh
set -eu

exec "$(dirname -- "$0")/start.mac.command" --host 0.0.0.0 "$@"
