#!/bin/bash
set -e
REPO=~/gastown
cd $REPO

git checkout main
for role in auth tests debug docs review; do
    git checkout -b polecat/$role 2>/dev/null || git checkout polecat/$role
    git checkout main
done

git push origin polecat/auth polecat/tests polecat/debug polecat/docs polecat/review --force

git worktree add ~/gastown/wt-auth    polecat/auth   2>/dev/null || true
git worktree add ~/gastown/wt-tests   polecat/tests  2>/dev/null || true
git worktree add ~/gastown/wt-debug   polecat/debug  2>/dev/null || true
git worktree add ~/gastown/wt-docs    polecat/docs   2>/dev/null || true
git worktree add ~/gastown/wt-review  polecat/review 2>/dev/null || true

echo "✓ Worktrees ready:"
git worktree list
