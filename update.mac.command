#!/bin/sh
set -eu

update_exit_code=0
"$(dirname -- "$0")/start.mac.command" --update || update_exit_code=$?

printf '\nPress Enter to close... '
IFS= read -r _ || :
exit "$update_exit_code"
