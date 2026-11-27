#!/usr/bin/env bash
# Runs as the default user inside the build container: install Guaardvark with the
# install.sh of the requested ref, the same script the public one-line install runs.
#   install-user.sh REPO_URL REF
set -euo pipefail
REPO="$1"
REF="$2"

src="$(mktemp -d)"
trap 'rm -rf "$src"' EXIT
git clone --quiet --depth 1 --branch "$REF" "$REPO" "$src/checkout"
echo "installing $(git -C "$src/checkout" rev-parse HEAD) ($REF), version $(cat "$src/checkout/VERSION" 2>/dev/null || echo unknown)"

# Model weights are not part of the image: Ollama starts empty and models are
# installed later from inside the app.
export GUAARDVARK_REPO_URL="$REPO"
export GUAARDVARK_BRANCH="$REF"
export GUAARDVARK_BOOTSTRAP_MODELS=0
set -a
. /usr/lib/guaardvark-image/image.env
set +a
bash "$src/checkout/install.sh"
