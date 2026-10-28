#!/bin/sh
# Every plugin's unit tests plus the shared client's. No live Herdr server needed.
set -e
cd "$(dirname "$0")/.."
for dir in lib git-status undo sort-spaces hold-done; do
    echo "== $dir"
    (cd "$dir" && python3 -B -m unittest discover -s tests)
done
