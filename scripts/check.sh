#!/usr/bin/env bash
# Run CI's checks locally (all but hassfest and the HACS action, which only run on GitHub).
#
#   scripts/check.sh             # everything below, in order
#   scripts/check.sh python      # pytest, ty, the vendored yarbo-local check
#   scripts/check.sh frontend    # typecheck and build into custom_components/yarbie/www
#   scripts/check.sh zip         # build yarbie.zip and run the tests against it unzipped
#
# Uses .venv and frontend/node_modules as they are; it never reinstalls them. CI starts
# from a clean `pip3 install -e '.[dev]'` and `npm ci`, so a package only your venv has
# can still pass here and fail there.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin"
cd "$ROOT"

step() { printf '\n== %s\n' "$*"; }

python_checks() {
  step pytest
  "$PY/pytest" -q
  step ty
  "$PY/ty" check custom_components tests
  step "vendored yarbo-local"
  python3 scripts/vendor_yarbo_local.py --check
}

frontend_checks() {
  step "frontend typecheck and build"
  (cd frontend && npm run typecheck && npm run build)
}

# The zip as HACS would install it, with only the tests and what they read beside it, so
# a file the integration needs but the zip lacks fails here.
zip_checks() {
  step yarbie.zip
  python3 scripts/build_release_zip.py
  # Not local: the EXIT trap runs after this function returns.
  check="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/yarbie-zipcheck.XXXXXX")"
  trap 'rm -rf "$check"' EXIT
  mkdir -p "$check/custom_components/yarbie"
  unzip -q yarbie.zip -d "$check/custom_components/yarbie"
  cp -R tests pyproject.toml "$check/"
  find "$check/tests" -name __pycache__ -prune -exec rm -rf {} +
  for dir in third_party blueprints scripts; do ln -s "$ROOT/$dir" "$check/$dir"; done
  step "pytest against the zip"
  (
    cd -P "$check"  # resolved, as Python reports its paths (/var is /private/var on macOS)
    "$PY/python" -c 'import custom_components.yarbie as m, sys; sys.exit(not m.__file__.startswith(sys.argv[1]))' "$PWD"
    "$PY/pytest" -q -p no:cacheprovider
  )
}

case "${1:-all}" in
  python) python_checks ;;
  frontend) frontend_checks ;;
  zip) zip_checks ;;
  all) python_checks; frontend_checks; zip_checks ;;
  *) echo "usage: $0 [python|frontend|zip]" >&2; exit 2 ;;
esac
step "all passed"
