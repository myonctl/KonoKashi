#!/bin/sh

script_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
bootstrap_python=${KONOKASHI_PYTHON:-python3}

if ! command -v "$bootstrap_python" >/dev/null 2>&1; then
    printf '%s\n' "KonoKashi setup needs Python 3.11 through 3.14: $bootstrap_python was not found." >&2
    exit 2
fi

exec "$bootstrap_python" "$script_directory/scripts/bootstrap_source.py" "$@"
