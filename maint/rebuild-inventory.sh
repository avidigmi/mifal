#!/bin/bash
# Rebuild the team lead's skill inventory from this tree, after any change under skills/.
# steward.py reads ~/.agents, so it runs in a disposable HOME whose Pool is this tree's skills/.
set -eu
repo="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
home="$(mktemp -d)"
trap 'rm -rf "${home:?}"' EXIT
home="$(cd -P "$home" && pwd)"
mkdir -p "$home/.agents/mifal"
ln -s "$repo/skills" "$home/.agents/skills"
ln -s "$repo/tools" "$home/.agents/mifal/tools"
cd "$repo"
env -i HOME="$home" PATH=/usr/bin:/bin TERM=dumb /usr/bin/python3 -B skills/team-lead/stewardship/steward.py build
