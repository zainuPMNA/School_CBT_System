#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Bundle every front-end asset LOCALLY so the CBT app needs zero CDN access.
#
# Run ONCE on any machine WITH internet (Linux, macOS, or Git Bash on Windows),
# then copy/commit the resulting files. The lab server never needs internet:
#
#   static/vendor/alpinejs/alpine.min.js
#   static/vendor/htmx/htmx.min.js
#   static/vendor/katex/{katex.min.css,katex.min.js,contrib/*,fonts/*}
#   static/css/app.css                      (compiled Tailwind)
#   bin/tailwindcss[.exe]                   (standalone CLI, git-ignored)
#
# Usage:
#   bash scripts/bundle_static.sh            # download vendors + build CSS
#   bash scripts/bundle_static.sh --css-only # rebuild CSS after editing templates
#                                            # (uses the already-downloaded CLI)
# ---------------------------------------------------------------------------
set -euo pipefail

TAILWIND_VERSION="3.4.17"
ALPINE_VERSION="3.14.8"
HTMX_VERSION="2.0.4"
KATEX_VERSION="0.16.11"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$ROOT/static/vendor"
BIN_DIR="$ROOT/bin"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

command -v curl >/dev/null || die "curl is required"
command -v tar  >/dev/null || die "tar is required"

# --- Detect platform for the Tailwind standalone binary ---------------------
case "$(uname -s)" in
  Linux*)               TW_OS="linux";   TW_EXT="" ;;
  Darwin*)              TW_OS="macos";   TW_EXT="" ;;
  MINGW*|MSYS*|CYGWIN*) TW_OS="windows"; TW_EXT=".exe" ;;
  *) die "Unsupported OS: $(uname -s)" ;;
esac
case "$(uname -m)" in
  x86_64|amd64)  TW_ARCH="x64" ;;
  aarch64|arm64) TW_ARCH="arm64" ;;
  *) die "Unsupported CPU architecture: $(uname -m)" ;;
esac
TAILWIND_BIN="$BIN_DIR/tailwindcss${TW_EXT}"

build_css() {
  [[ -x "$TAILWIND_BIN" ]] || die "Tailwind CLI not found at $TAILWIND_BIN — run without --css-only first."
  log "Building static/css/app.css with Tailwind v${TAILWIND_VERSION}"
  mkdir -p "$ROOT/static/css"
  (cd "$ROOT" && "$TAILWIND_BIN" -c tailwind.config.js -i assets/tailwind/input.css -o static/css/app.css --minify)
  log "CSS size: $(wc -c < "$ROOT/static/css/app.css") bytes"
}

if [[ "${1:-}" == "--css-only" ]]; then
  build_css
  exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# fetch_npm <package> <version> -> extracts to $TMP/<basename>/package
fetch_npm() {
  local name="$1" version="$2" base
  base="${name##*/}"
  log "Downloading ${name}@${version} from the npm registry"
  curl -fsSL "https://registry.npmjs.org/${name}/-/${base}-${version}.tgz" -o "$TMP/${base}.tgz"
  mkdir -p "$TMP/${base}"
  tar -xzf "$TMP/${base}.tgz" -C "$TMP/${base}"
}

# --- Alpine.js -----------------------------------------------------------------
fetch_npm alpinejs "$ALPINE_VERSION"
mkdir -p "$VENDOR/alpinejs"
cp "$TMP/alpinejs/package/dist/cdn.min.js" "$VENDOR/alpinejs/alpine.min.js"

# --- HTMX ------------------------------------------------------------------------
fetch_npm htmx.org "$HTMX_VERSION"
mkdir -p "$VENDOR/htmx"
cp "$TMP/htmx.org/package/dist/htmx.min.js" "$VENDOR/htmx/htmx.min.js"

# --- KaTeX (CSS, JS, auto-render, mhchem for chemistry, all fonts) -------------------
fetch_npm katex "$KATEX_VERSION"
rm -rf "$VENDOR/katex"
mkdir -p "$VENDOR/katex/contrib"
cp    "$TMP/katex/package/dist/katex.min.css"            "$VENDOR/katex/"
cp    "$TMP/katex/package/dist/katex.min.js"             "$VENDOR/katex/"
cp    "$TMP/katex/package/dist/contrib/auto-render.min.js" "$VENDOR/katex/contrib/"
cp    "$TMP/katex/package/dist/contrib/mhchem.min.js"    "$VENDOR/katex/contrib/"
cp -R "$TMP/katex/package/dist/fonts"                    "$VENDOR/katex/fonts"

# --- Tailwind standalone CLI (no Node.js needed) -------------------------------
mkdir -p "$BIN_DIR"
TW_URL="https://github.com/tailwindlabs/tailwindcss/releases/download/v${TAILWIND_VERSION}/tailwindcss-${TW_OS}-${TW_ARCH}${TW_EXT}"
log "Downloading Tailwind CLI: $TW_URL"
curl -fsSL "$TW_URL" -o "$TAILWIND_BIN"
chmod +x "$TAILWIND_BIN"

build_css

# --- Record what was bundled -------------------------------------------------
{
  echo "Bundled $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "alpinejs ${ALPINE_VERSION}"
  echo "htmx.org ${HTMX_VERSION}"
  echo "katex ${KATEX_VERSION}"
  echo "tailwindcss ${TAILWIND_VERSION}"
} > "$VENDOR/VERSIONS.txt"
rm -f "$VENDOR"/*/.gitkeep

log "Done. All assets are local. Next on the server: python manage.py collectstatic --noinput"
