#!/usr/bin/env bash
# Fetch runner-produced evidence and build products from the outputs branch.
#
# Only the paths the runners actually produce are checked out. Checking out
# evidence/ wholesale would overwrite files the session computes (missing.json,
# runtime_lookups.md, shims.md, exports/bundle) with the stale copies that were
# in the runner's checkout, which silently reverted missing.json once.
set -euo pipefail
cd "$(dirname "$0")/.."

BRANCH="${1:-outputs}"

for i in 1 2 3 4; do
  if git fetch origin "$BRANCH"; then break; fi
  echo "fetch failed, retrying in $((2 ** i))s"; sleep $((2 ** i))
done

echo "outputs branch is at: $(git log --oneline -1 "origin/$BRANCH")"

# Runner-produced only. exports/bundle/ is session-computed and excluded.
PATHS=()
while IFS= read -r p; do PATHS+=("$p"); done < <(
  git ls-tree -r --name-only "origin/$BRANCH" \
    | grep -E '^(evidence/exports/(device|sdk)_|evidence/sdk/|evidence/device_queries/|out/shims/)' \
    || true)

if [ ${#PATHS[@]} -eq 0 ]; then
  echo "nothing matched on origin/$BRANCH" >&2
  exit 1
fi

printf '%s\n' "${PATHS[@]}" | sed 's#/[^/]*$##' | sort -u | sed 's/^/  /'
git checkout "origin/$BRANCH" -- "${PATHS[@]}"
echo "checked out ${#PATHS[@]} files from origin/$BRANCH"
