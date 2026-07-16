#!/usr/bin/env bash
# Bootstrap an environment (local dev or CI).
#
# The BLN packages can't be installed straight from git: warn-scraper's
# setup.py imports jinja2, us, and the warn package itself at build time
# without declaring build requirements. So we install its deps first, then
# clone at a pinned SHA and install with --no-build-isolation and PYTHONPATH
# pointing at the clone.
set -euo pipefail
cd "$(dirname "$0")"

WARN_SCRAPER_SHA=f7b3dd26af1f3ad700762504bd7c5c0d23979507
WARN_TRANSFORMER_SHA=82454b5b767e2b7fa42085f23799f34292996b90

PYTHON="${PYTHON:-python3}"
if [ ! -d .venv ]; then
  "$PYTHON" -m venv .venv
fi
PIP=.venv/bin/pip

$PIP install --quiet --upgrade pip
$PIP install --quiet -r requirements.txt

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
for repo_sha in "warn-scraper $WARN_SCRAPER_SHA" "warn-transformer $WARN_TRANSFORMER_SHA"; do
  set -- $repo_sha
  repo=$1 sha=$2
  git clone --quiet https://github.com/biglocalnews/$repo "$tmp/$repo"
  git -C "$tmp/$repo" checkout --quiet "$sha"
  (cd "$tmp/$repo" && PYTHONPATH="$tmp/$repo" $PIP install --quiet --no-build-isolation .)
done

$PIP install --quiet -e '.[dev]'
echo "Done. Activate with: source .venv/bin/activate"
