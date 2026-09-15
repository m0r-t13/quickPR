#!/bin/sh
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
    printf 'Please install Python 3.9+ from https://www.python.org/downloads/\n'
    read -r answer
    exit 1
fi
python3 -m quickpr "$@"
result=$?
if [ "$#" -eq 0 ]; then
    printf '\nPress Enter to close...'
    read -r answer
fi
exit "$result"
