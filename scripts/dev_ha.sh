#!/usr/bin/env bash
# Run a throwaway Home Assistant on http://127.0.0.1:8123 with this integration linked in.
#
#   scripts/dev_ha.sh            # start (first run builds .venv-ha and .ha-dev)
#   (cd frontend && npm run watch)   # in another terminal: rebuild the panel on save
#
# Add the robot under Settings > Devices & services > Add integration > yarbie; typing
# "simulator" as the address gives the demo yard. Restart HA to pick up a rebuilt panel.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv-ha"
CONFIG="$ROOT/.ha-dev"
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

mkdir -p "$CONFIG/custom_components"
ln -sfn "$ROOT/custom_components/yarbie" "$CONFIG/custom_components/yarbie"
if [ ! -f "$CONFIG/configuration.yaml" ]; then
  cat > "$CONFIG/configuration.yaml" <<'YAML'
default_config:
http:
  server_host: 127.0.0.1  # this HA has a throwaway login; keep it off the LAN
logger:
  default: warning
  logs:
    custom_components.yarbie: debug
YAML
fi

exec "$VENV/bin/hass" -c "$CONFIG"
