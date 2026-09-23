#!/bin/sh
# Create an empty private root at local/ (git-ignored) for real notes.
# The committed vault/ is a synthetic demo; never put real notes there.
#   sh setup_local.sh        then   python3 memory.py --root local <command> ...
set -eu
cd "$(dirname "$0")"
if [ -e local ]; then echo "local/ already exists; nothing changed" >&2; exit 1; fi
mkdir -p local/vault
cp routes.json ROUTING.md local/
printf '# Current state\n\n## Focus\n\n## Constraints\n' > local/vault/CURRENT_STATE.md
printf '# Tasks\n\n## NOW\n\n## NEXT\n\n## LATER\n' > local/vault/TASKS.md
printf '# Decisions\n\n| decision | reason |\n| --- | --- |\n' > local/vault/DECISIONS.md
for c in ideas jobs projects sessions; do
  mkdir -p "local/vault/$c"
  cp "vault/$c/_TEMPLATE.md" "local/vault/$c/"
  printf '# %s index\n\nTitle, path and one-line description only. Status lives in the note itself.\n\n| title | path | description |\n| --- | --- | --- |\n' "$c" > "local/vault/$c/INDEX.md"
done
echo "created local/ (git-ignored). Use: python3 memory.py --root local context <route> [<slug>]"
