# yarbie

A map editor for Yarbo robots that draws the robot's map over satellite imagery. You can
reshape and rename work areas, and draw no-go zones, pathways and sidewalks. You can also
edit plans and per-area mowing settings, and preview the route the robot would take, all
from the browser.

## How it talks to the robot

| Source | Set with `YARBIE_SOURCE` | Can change the robot? |
|---|---|---|
| `sim` | A built-in simulated robot with a demo yard (the default) | Yes (the simulated one) |
| `lan` | The robot's own MQTT broker on your network, via [yarbo-local](https://github.com/yarbo-local/yarbo-local) | Yes |
| `cloud` | Yarbo's cloud, via the official `yarbo-data-sdk` | No, view and export only |

Writes use only commands that yarbo-local marks as *verified*: captured from the official
phone app on firmware 3.14.11 and replayed on a real robot. So the robot stores the same
records the app would. The app sees the changes when it next loads the map, and can edit
them like anything drawn in the app.

| Map object | Create | Change | Delete |
|---|---|---|---|
| No-go zones | ✓ | ✓ shape, name, on/off | ✓ |
| Pathways, sidewalks | ✓ | ✓ | ✓ |
| Work areas | export only* | ✓ shape, name, mowing pattern | export only |
| Plans, mowing settings | ✓ | ✓ | ✓ (plans) |

\* The robot maps a new area by driving round it, and there is no verified delete command.

Safety rails:
- Every apply first saves the robot's full map to `data/backups/`.
- An apply is refused if the map changed since you loaded it, for example from the app.
- Writes are refused while a plan is running or paused.
- Apply needs a second click to confirm.
- The change list warns when a pathway loses its link to the dock. Without one, plans that
  start from the dock fail with WP005.
- The server listens on loopback only. It has no login, and anyone who can reach it can
  change the robot's map.

## Run

```sh
python3 -m venv .venv && .venv/bin/pip3 install -e '.[dev]'
(cd frontend && npm ci && npm run build)
cp .env.example .env            # choose sim / lan / cloud
.venv/bin/yarbie                # http://127.0.0.1:8765
```

For frontend development, run `.venv/bin/yarbie` and `cd frontend && npm run dev`
(http://localhost:5173, which proxies `/api`).

### Finding the robot

With `YARBIE_SOURCE=lan` and no `YARBO_HOST`, yarbie looks for the robot when it starts.
It scans this machine's /24 (or `YARBIE_SCAN`) for an MQTT broker on port 1883 carrying
a Yarbo heartbeat. It remembers the address in `data/lan_robot.json` and checks it again
on the next start, scanning only if the robot has moved. Set `YARBO_SERIAL` if you have
more than one robot. To see what is on the network:

```sh
.venv/bin/yarbie discover                  # or: yarbie discover 10.0.0.0/24
```

### Docker

```sh
cp .env.example .env            # e.g. YARBIE_SOURCE=lan, YARBO_HOST=192.168.1.50
docker compose up --build       # http://127.0.0.1:8765, backups in ./data
```

The image builds the UI and serves it at `/`. With `lan`, the container must be able to
reach the robot on port 1883. Docker's default bridge network can reach your LAN on
macOS and Linux. Auto-detection can't see your LAN's address range from inside a
container, so set `YARBIE_SCAN` (e.g. `192.168.1.0/24`) or `YARBO_HOST` in `.env`.

## First time on a real robot

Do this once (and again after a firmware update) before trusting writes:

1. `YARBIE_SOURCE=lan`, open yarbie, and check the outlines sit on your lawn. If the photo
   is off, use **Imagery alignment** (display only).
2. Draw a small no-go zone, apply it, and confirm the app shows it and can edit and
   delete it.
3. Draw one in the app, **Reload** yarbie, and confirm it appears.
4. Delete the test zone from yarbie.

## Checks

```sh
.venv/bin/pytest                # diff logic and the API against the simulator
.venv/bin/ty check backend tests
(cd frontend && npm run typecheck)
```
