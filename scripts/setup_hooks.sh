#!/bin/sh
# Run once after cloning. Points git at the versioned hooks in scripts/hooks.
set -e
cd "$(git rev-parse --show-toplevel)"
git config core.hooksPath scripts/hooks
chmod +x scripts/hooks/* scripts/*.py 2>/dev/null || true
echo "hooks enabled -> $(git config core.hooksPath)"
if [ ! -f private/denylist.txt ] && [ -z "$SCAN_DENYLIST" ]; then
  echo
  echo "WARNING: no private/denylist.txt and no \$SCAN_DENYLIST."
  echo "Commits will be BLOCKED until you clone arch-ive-private to ./private/."
fi
