#!/usr/bin/env bash
# Commit derived evidence / built artefacts to the outputs branch.
#
# Called from the macOS runners. Two jobs can finish at the same time, so the
# push is retried against a fresh fetch of the branch. Only paths that are
# allowed to leave the runner are added (see CLAUDE.md "Hard data rules"):
# derived text under evidence/, and our own built dylibs/dSYMs under out/shims.
# Nothing from the Roblox bundle and no Apple SDK bytes are ever present here.
set -euo pipefail

BRANCH="${1:?outputs branch}"
MESSAGE="${2:-runner outputs}"

ALLOW=(evidence out/shims)

git config user.name  "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# Stash the produced files outside the worktree so switching branches is clean.
STAGE=$(mktemp -d)
for p in "${ALLOW[@]}"; do
  if [ -e "$p" ]; then
    mkdir -p "$STAGE/$(dirname "$p")"
    cp -R "$p" "$STAGE/$(dirname "$p")/"
  fi
done

for attempt in 1 2 3 4 5; do
  git fetch origin "$BRANCH" --depth 1 2>/dev/null || true
  if git rev-parse --verify "origin/$BRANCH" >/dev/null 2>&1; then
    git checkout -B "$BRANCH" "origin/$BRANCH"
  else
    # orphan branch: outputs carries only runner products, no source history
    git checkout --orphan "$BRANCH"
    git rm -rf --cached . >/dev/null 2>&1 || true
    find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} + 2>/dev/null || true
  fi

  for p in "${ALLOW[@]}"; do
    if [ -e "$STAGE/$p" ]; then
      mkdir -p "$(dirname "$p")"
      rm -rf "$p"
      cp -R "$STAGE/$p" "$(dirname "$p")/"
      git add -A "$p"
    fi
  done

  if git diff --cached --quiet; then
    echo "no changes to commit"
    exit 0
  fi
  git commit -m "$MESSAGE (run ${GITHUB_RUN_ID:-local})"
  if git push origin "$BRANCH"; then
    echo "pushed to $BRANCH"
    exit 0
  fi
  echo "push failed, retrying ($attempt)"
  sleep $((attempt * 5))
done
echo "could not push to $BRANCH" >&2
exit 1
