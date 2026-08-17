#!/usr/bin/env bash
# Full-sync deploy of the committed tree to the eucstats droplet.
#
#   ./scripts/deploy.sh              # deploy HEAD (must equal origin/main)
#   ./scripts/deploy.sh --check      # verify only, change nothing
#
# Ships EVERY tracked file, not a hand-picked subset. A selective file-push once
# half-applied and broke all uploads; `git archive` of a commit cannot do that,
# because the deployed tree is exactly that commit. data/, .venv/ and anything in
# .gitignore are never in the archive, so live data and secrets are untouched.
#
# Auth is whatever ssh already uses - an installed key, or it prompts you for the
# password. Nothing is stored or echoed by this script.
set -euo pipefail

HOST="${EUCSTATS_HOST:-root@64.227.89.199}"
APP="${EUCSTATS_APP_DIR:-/opt/eucstats}"
SERVICE="${EUCSTATS_SERVICE:-eucstats}"
URL="${EUCSTATS_URL:-https://eucstats.ried.no}"

verify() {
    echo
    echo "--- verifying ---"
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$URL/health" || echo "000")
    echo "  /health                 : $code $([ "$code" = 200 ] && echo OK || echo FAILED)"
    local page
    page=$(curl -s --max-time 30 "$URL/" || true)
    case "$page" in
        *IMPERIAL_TZ*) echo "  units fix (timezone)    : LIVE" ;;
        *MPH_REGIONS*) echo "  units fix (timezone)    : NOT live - still old code" ;;
        *)             echo "  units fix (timezone)    : could not read page" ;;
    esac
    ssh "$HOST" "systemctl is-active $SERVICE" 2>/dev/null \
        | sed 's/^/  service                 : /' || echo "  service                 : unknown"
}

if [ "${1:-}" = "--check" ]; then
    verify
    exit 0
fi

# Deploy only what is committed AND pushed, so the server always matches a commit
# you can point at later.
if [ -n "$(git status --porcelain)" ]; then
    echo "working tree is dirty - commit or stash first:" >&2
    git status --short >&2
    exit 1
fi
git fetch -q origin
local_sha=$(git rev-parse HEAD)
remote_sha=$(git rev-parse origin/main)
if [ "$local_sha" != "$remote_sha" ]; then
    echo "HEAD is not origin/main - push first" >&2
    echo "  HEAD        $local_sha" >&2
    echo "  origin/main $remote_sha" >&2
    exit 1
fi

echo "deploying to $HOST:$APP"
git log --oneline -1 | sed 's/^/  /'
echo

echo "[1/3] syncing tracked files"
git archive HEAD | ssh "$HOST" "mkdir -p '$APP' && tar -x -C '$APP'"

echo "[2/3] installing deps (no cache - the droplet root fills up)"
ssh "$HOST" "cd '$APP' && .venv/bin/pip install --quiet --no-cache-dir -r requirements.txt"

echo "[3/3] clearing stale bytecode and restarting"
ssh "$HOST" "find '$APP' -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null; systemctl restart '$SERVICE'"

sleep 3
verify

cat <<'NOTE'

Note: this adds and overwrites tracked files but does not delete files that were
removed from git. That is deliberate - nothing is rm -rf'd on a live box. If a
file is ever deleted in a commit, remove it on the server by hand.
NOTE
