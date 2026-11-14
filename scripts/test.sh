#!/bin/sh
# Every plugin's unit tests plus the shared client's. No live Herdr server needed.
set -e
# Fixture repositories must not run the user's global git hooks (commit-msg style) or signing.
export GIT_CONFIG_GLOBAL=/dev/null
cd "$(dirname "$0")/.."
for dir in lib git-status undo sort-spaces cycle worktree-reaper; do
    echo "== $dir"
    (cd "$dir" && python3 -B -m unittest discover -s tests)
done
