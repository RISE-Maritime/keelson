#!/usr/bin/env bash

set -euo pipefail

echoerr() { echo "$@" 1>&2; }

echoerr "Keelson is starting up..."
echoerr "Executing: $BASH_EXECUTION_STRING"

exit() {
    echoerr "Keelson is shutting down..."
}

trap exit SIGINT SIGTERM
