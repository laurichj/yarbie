#!/usr/bin/env bash
# Run a throwaway Home Assistant on http://127.0.0.1:8123 with this integration linked in.
#
#   scripts/dev_ha.sh            # start (first run builds .venv-ha and .ha-dev)
#   HA_PORT=8124 scripts/dev_ha.sh   # another port, e.g. from a worktree beside the main one
#   (cd frontend && npm run watch)   # in another terminal: rebuild the panel on save
#
# Each checkout or worktree gets its own .venv-ha and .ha-dev. The panel comes from
# custom_components/yarbie/www, which git does not hold: build it first (npm run build).
#
# Add the robot under Settings > Devices & services > Add integration > yarbie; typing
# "simulator" as the address gives the demo yard. Restart HA to pick up a rebuilt panel.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv-ha"
CONFIG="$ROOT/.ha-dev"
PORT="${HA_PORT:-8123}"
HA_VERSION="$("$ROOT/.venv/bin/python" -c 'from homeassistant.const import __version__; print(__version__)')"

if [ ! -x "$VENV/bin/hass" ]; then
  python3 -m venv "$VENV"
  FRONTEND="$("$ROOT/.venv/bin/python" -c 'import json, homeassistant.components.frontend as f, pathlib; print(json.loads((pathlib.Path(f.__file__).parent / "manifest.json").read_text())["requirements"][0])')"
  "$VENV/bin/pip3" install "homeassistant==$HA_VERSION" "$FRONTEND"
  # Voice components HA loads by default build C++ that needs the SDK headers on macOS.
  if [ "$(uname)" = "Darwin" ]; then
    SDK="$(xcrun --show-sdk-path)"
    CXXFLAGS="-isysroot $SDK -I$SDK/usr/include/c++/v1" CFLAGS="-isysroot $SDK" \
      "$VENV/bin/pip3" install pymicro-vad pyspeex-noise
  fi
fi

if [ ! -f "$ROOT/custom_components/yarbie/www/yarbie-panel.js" ]; then
  echo "note: the frontend is not built, so HA will run without the panel; run: (cd frontend && npm ci && npm run build)" >&2
fi

mkdir -p "$CONFIG/custom_components"
ln -sfn "$ROOT/custom_components/yarbie" "$CONFIG/custom_components/yarbie"
if [ ! -f "$CONFIG/configuration.yaml" ]; then
  cat > "$CONFIG/configuration.yaml" <<YAML
default_config:
http:
  server_host: 127.0.0.1  # this HA has a throwaway login; keep it off the LAN
  server_port: $PORT
logger:
  default: warning
  logs:
    custom_components.yarbie: debug
YAML
else
  # The port is written once, with the config; a different HA_PORT later would be ignored.
  configured="$(sed -n 's/^  server_port: *\([0-9][0-9]*\).*/\1/p' "$CONFIG/configuration.yaml")"
  if [ "${configured:-8123}" != "$PORT" ]; then
    echo "$CONFIG/configuration.yaml uses port ${configured:-8123}, not $PORT: run with HA_PORT=${configured:-8123}, or edit its http: server_port" >&2
    exit 1
  fi
fi

echo "Home Assistant on http://127.0.0.1:$PORT (config $CONFIG)"
exec "$VENV/bin/hass" -c "$CONFIG"
