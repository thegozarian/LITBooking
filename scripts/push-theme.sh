#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "Run this script from inside the LITBooking Git repository." >&2
  exit 1
}
cd "$repo_root"

branch="$(git branch --show-current)"
if [[ "$branch" != "main" ]]; then
  echo "Refusing to push from '$branch'; check out main first." >&2
  exit 1
fi

# Only Shopify theme folders are included; backend and project files stay out
# of the theme deployment commit.
git add -- assets config layout locales sections snippets templates

if ! git diff --cached --quiet; then
  commit_message="${1:-Update Shopify theme}"
  git commit -m "$commit_message"
else
  echo "No uncommitted Shopify theme changes to commit."
fi

# Shopify can write theme-editor changes back to GitHub. Rebase those after
# making the local commit so a conflict leaves all work safely committed.
git pull --rebase origin main
git push origin main

echo "Theme changes pushed. Shopify will sync the connected main branch."
