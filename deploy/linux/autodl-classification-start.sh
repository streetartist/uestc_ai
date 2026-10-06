#!/usr/bin/env bash
set -eu
umask 077
config=${1:-/etc/uestc-classification.env}
test "$(id -u)" = 0
test -f "$config"
test "$(stat -c %a "$config")" = 600
set -a
. "$config"
set +a
exec /opt/uestc-classification/runtime/bin/python /opt/uestc-classification/backend/classification_worker.py
