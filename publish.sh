#!/usr/bin/env bash
# publish.sh — copy the Trend Radar files into your GitHub repo and push them.
#
#   bash publish.sh                       # push to https://github.com/booluckgmie/market-sentiment
#   bash publish.sh "my commit message"
#   REPO=git@github.com:booluckgmie/market-sentiment.git bash publish.sh   # use SSH instead of HTTPS
#   BRANCH=main bash publish.sh           # push to a specific branch (default: the repo's default branch)
#
# It clones the repo to a temporary folder, copies the files in (overwriting older versions),
# commits and pushes. Your existing history is kept. No credentials are stored by this script:
# git asks for them the normal way (credential manager, SSH key, or `gh auth login` / `gh auth setup-git`).
set -euo pipefail

REPO="${REPO:-https://github.com/booluckgmie/market-sentiment.git}"
MSG="${1:-Update Malaysia Trend Radar}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES=(refresh.py notify.py topics.py requirements.txt README.md .gitignore publish.sh vercel.json .github/workflows/daily.yml index.html)

command -v git >/dev/null || { echo "git is not installed."; exit 1; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

echo "→ Cloning $REPO"
git clone --quiet "$REPO" "$TMP/repo" 2>&1 | grep -v "empty repository" || true
[ -d "$TMP/repo/.git" ] || { echo "Could not clone. Check the repo address and that you are signed in to GitHub."; exit 1; }
cd "$TMP/repo"

CURRENT="$(git branch --show-current || true)"           # empty when the repo has no commits yet
TARGET="${BRANCH:-${CURRENT:-main}}"
[ "$TARGET" = "$CURRENT" ] || git checkout --quiet -B "$TARGET"
echo "→ Branch: $TARGET"

copied=0
for f in "${FILES[@]}"; do
  if [ -f "$HERE/$f" ]; then
    mkdir -p "$(dirname "$f")"; cp "$HERE/$f" "$f"; copied=$((copied+1))
  fi
done
echo "→ Copied $copied files"

git add -A
if git diff --cached --quiet; then
  echo "✓ Nothing to push: the repo already matches these files."; exit 0
fi
git config user.name  >/dev/null 2>&1 || git config user.name  "Trend Radar"
git config user.email >/dev/null 2>&1 || git config user.email "trend-radar@users.noreply.github.com"
git commit --quiet -m "$MSG"
echo "→ Pushing"
git push --quiet -u origin "$TARGET"

echo
echo "✓ Done. Files are on GitHub."
echo "  Next (first time only), see README.md for details:"
echo "   1. Repo → Actions → Daily Trend Radar refresh → Run workflow  (creates the vercel-site branch)"
echo "   2. vercel.com → Add New → Project → import this repo (Framework Preset: Other)"
echo "   3. Vercel → Settings → Environments → Production → Branch Tracking → vercel-site"
echo "   4. Run the workflow once more, then open your Vercel address"
