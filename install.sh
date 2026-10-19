#!/bin/bash
# Mifal installer: ./install.sh [--dry-run] [--harness NAME...] [--herdr-script]
set -eu

here="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "mifal: this release supports macOS only (this system is $(uname -s))." >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1 || ! python3 -B -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    echo "mifal: python3 3.9 or later is required. Install the Xcode Command Line Tools with: xcode-select --install" >&2
    exit 1
fi

exec /bin/bash "$here/bin/mifal" install "$@"
