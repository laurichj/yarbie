# yarbie as a Home Assistant integration

Status: being built · 2026-10-22 · replaces `remote-design.md` (GCP), which is no longer pursued.

## Implementation status

Done on branch `ha-integration` (phases 0–6, checked in a local HA 2026.10.2 with the
simulator):

- Baseline commit; yarbo-local vendored.
- The integration: config flow, coordinator, all platforms in the entity table, actions,
  diagnostics, the firmware repair, the kill switch and observe-only mode.
- The editor's websocket API and jobs.
- The panel.
- The map image entity, blueprints, HACS manifest, `scripts/dev_ha.sh`.
- The Yarbo dashboard strategy (Settings, Dashboards, Add dashboard, Yarbo).
- The run history (TODO 1):
  - the robot's records joined with yarbie's run log in `/config/yarbie/runs`
  - the panel's Runs section
  - last-run sensors
  - a retention option

Where the build differs from the plan below:

- **Vendoring layout.** The subtree holds the whole upstream repo at
  `third_party/yarbo-local`. `scripts/vendor_yarbo_local.py` copies the package and its
  `protocol/*.yaml` into `custom_components/yarbie/_vendor/yarbo_local`, the way a wheel
  would install them. HACS installs only `custom_components/yarbie`, and upstream keeps
  its data outside the package.
  - One patch: the data directory is found by package name.
  - `--check` fails on a stale or hand-edited copy.
  - The subtree came from upstream at the pinned commit. Later pulls come from the
    fork, `laurichj/yarbo-local` (remote `yarbo-local`).
- **No `TESTED_FIRMWARE` constant.** The firmware rail reads the registry's own verified
  firmware, so there is nothing to keep in sync.
- **`probatio`, not `voluptuous`.** HA 2026.10's own integrations validate with
  `probatio`, so yarbie does too.
- **The panel runs in its own iframe** (`embed_iframe`), so `main.ts` kept its element
  lookups and styles.
  - Replies are copied into the iframe before use, because MapLibre's worker refuses
    objects made in HA's window.
- **The status pill polls `yarbie/info`.** It is answered from the session's state, so the
  robot is never asked. It does not read `hass.states`.
- **No wireless charger output sensors yet.** The scale of those fields is unresolved in
  the field map.

Not done yet:

- Phase 7 (migration on the real robot). Phase 8 (removing `backend/` and the Docker
  files) was done ahead of it, before merging.
- The panel's phone layout and HA theme colours.
- Entity icons.
- The error sensor as an enum with translated fault names.
- The run history shows past runs on today's map. Only the map revision is kept, not a
  copy of the map as it was.

## Goal

Run yarbie full time on the Home Assistant Green, as the one piece of software that talks
to the Yarbo. Remote access is whatever HA already has. One integration that we own does
three jobs, using one connection to the robot:

- the robot's entities, actions and blueprints. This replaces markus-lassfolk's
  `home-assistant-yarbo` and its `python-yarbo` library;
- the map editor, as a sidebar panel inside HA;
- map backups and the safety rails around every write.

We drop the standalone FastAPI server and the Docker image. There is no non-HA run mode.
Development uses a local HA container with the simulator.

Only the mower is in scope. Snow and leaf blower heads are out.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Where it runs | A custom integration (`custom_components/yarbie`) inside HA Core, installed through HACS from our repo | No container, no Ingress, no second login. Editor and entities share one robot session |
| Existing integrations | Replaced, not built on | Reimplementing is cheap with yarbo-local, and one owner removes contention for the robot's controller role |
| Protocol library | **yarbo-local**, forked and vendored with `git subtree` | It has a registry of 57 commands verified against the phone app and refuses anything else. All its internal imports are relative, so it vendors cleanly |
| Library dropped | `python-yarbo` | It exposes raw `send_command`, which is exactly what the rails exist to stop |
| Editor UI | The existing MapLibre/TypeScript editor, rewrapped as an HA custom panel | Keeps the work already done. It talks to the backend over HA's websocket |
| Server stack dropped | FastAPI, uvicorn, pydantic, python-dotenv, httpx, Dockerfile, compose, env `config.py`, `cloud` source, `yarbo-data-sdk` | HA provides HTTP, websocket, auth, config and storage |
| Remote access | HA's own (companion app or whatever remote access HA has) | No separate exposure |

## Why one owner of the robot matters

Every robot command (map writes, lights, dock, plans) first takes the robot's single
**controller role**. Two HA integrations that both send commands would take it from each
other. yarbo-local also caches whether it holds the role until the connection drops, so a
handover could go unnoticed. With one integration, the only other thing that can take the
role is the Yarbo phone app. Reads never take it.

## Repo layout

```
custom_components/yarbie/
  manifest.json  __init__.py  const.py  config_flow.py  coordinator.py
  lawn_mower.py sensor.py binary_sensor.py button.py select.py switch.py
  light.py device_tracker.py event.py image.py
  diagnostics.py  repairs.py  services.yaml  strings.json  translations/en.json
  websocket.py    editor API (websocket commands and job subscriptions)
  panel.py        registers the sidebar panel and serves www/
  editor/
    mapdoc.py diff.py heatmap.py demo.py    moved unchanged from backend/yarbie
    apply.py      today's MapService.apply/_send and rails
    backups.py    today's BackupStore, plus actor and metadata
    discovery.py  today's discovery, plus a DHCP hint
    imagery.py    Google Map Tiles session, on HA's aiohttp session and Store
    jobs.py       long-running robot calls (route preview, history read)
  _vendor/yarbo_local/                      git subtree of our fork
  www/yarbie-panel.js                       built from frontend/
frontend/            TypeScript source. A Vite library build writes www/
blueprints/automation/yarbie/
tests/               pytest-homeassistant-custom-component, against the simulator
hacs.json
docs/ha-design.md  docs/mockups/
```

`backend/`, `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.env.example` and
`pyproject.toml`'s server entry points are gone (phase 8). `pyproject.toml` stays for dev
tools: pytest, `ty`, the HA test harness, and the integration's `aiomqtt` requirement.

## Library: vendoring and staying in sync

- Fork `yarbo-local/yarbo-local` to our GitHub. Bring it in with
  `git subtree add --prefix custom_components/yarbie/_vendor/yarbo_local …`, starting from
  the commit pinned today (`395bdb5`).
- Updates come in with `git subtree pull` as **one commit**, with the upstream SHA in the
  message. Never edit the vendored copy by hand. Fixes go to the fork first, then come in
  by subtree.
- `manifest.json` requirements are `aiomqtt==<pin>` only. aiomqtt needs `paho-mqtt>=2.1`,
  which HA already ships, and `pyyaml` is already in HA.
- **A CI check** keeps the vendored `commands.yaml` version and its tested firmware equal
  to `const.TESTED_FIRMWARE`.
- **A rail in HA:**
  - If the robot reports firmware outside the tested set, raise a Repairs issue and turn
    **map, plan and settings writes off** until you confirm after redoing the first-time
    checks.
  - Controls (start, pause, dock, lights) keep working.
  - Mockup: `mockups/06-repairs-firmware.png`.

## Integration

### Config flow

- **DHCP discovery** on the robot's MAC prefix `C8:FE:0F:*`.
- **Manual host** entry.
- If neither is set, a **heartbeat scan** of the subnet HA is on. That is today's
  `discovery.locate`, recognising the robot by the serial in its heartbeats.
- The last address is kept in the config entry and checked again at start-up. A rescan
  happens only if the robot has moved.
- A hidden **simulator** option (developer mode only) runs the demo yard. Tests and local
  development use it.

**Options:**
- Scan subnet.
- Google Maps key (optional; free imagery is built into the panel).
- Keep awake.
- **Allow map writes**: the kill switch, on by default.
- **Observe only**: never takes the controller. Used during migration.

### Coordinator

- One `YarboRobot` session per robot. Telemetry pushes update entities, with no polling.
- The map is cached by revision and read again when the broker reports a change (for
  example, an edit in the phone app) or after an apply.
- The plan lifecycle comes from yarbo-local's `lifecycle.py` and feeds the `event` entity
  and the run log (see the run history TODO).
- High-rate telemetry is **not** turned into HA states unless an entity asks for it. See
  "Entity defaults".

### Entities

Mockups: `mockups/01-dashboard.png`, `mockups/02-device.png`.

| Platform | Entity | Default |
|---|---|---|
| `lawn_mower` | Yarbo: start, pause, dock | on |
| `select` | Plan | on |
| `sensor` | Activity, battery, plan status, plan progress, time remaining, error code, head type | on |
| `sensor` | RTK status | on |
| `sensor` | Satellites, heading, battery voltage, battery current | off |
| `sensor` | Wireless charger output voltage and current, blade rpm (L/R), wheel speed (L/R) | off |
| `binary_sensor` | Charging, problem, rain, emergency stop, RTK fix, planning | on |
| `binary_sensor` | Wheel, power and push-pod faults | off (they also feed "problem") |
| `button` | Beep (`song_cmd`), return to dock, pause, resume, stop, wake, refresh map | on |
| `light` | All lights (`light_ctrl`) | on |
| `light` | 7 individual channels | off |
| `switch` | Sound (`set_sound_param`), person detection, child lock | on |
| `device_tracker` | Position, from `CombinedOdom` plus the GPS reference | on |
| `event` | Plan lifecycle | on |
| `image` | Map render (areas, no-go zones, pathways, dock, robot, this run's progress) | on |

**Entity defaults.** HA's recorder writes every state change to its database, which on the
Green means eMMC writes and database growth.
- Turned **off** by default: per-second diagnostic fields (currents, voltages, wheel and
  blade speeds, heading).
- Turned **on**: everything you'd read or automate on. The choice is ours; this table is
  the starting point.

### Actions

- `yarbie.start_plan` (by name or id).
- `yarbie.set_lights` (all channels or per channel).
- `yarbie.get_map`: returns GeoJSON as response data.
- Pause, resume and return to dock also work through the `lawn_mower` actions and buttons.
- Deliberately **no** raw command action.

### Other HA features used

- **Diagnostics download**, redacted with yarbo-local's `redact.py` (serial, MAC, IP,
  coordinates, Wi-Fi).
- **Repairs**: the untested-firmware issue above, plus "robot not found" with a rescan
  fix.
- **Logbook and persistent notifications**: after every apply, "Map changed by
  &lt;user&gt; in Yard editor · N changes · backup &lt;name&gt;".
- **Blueprints**, ported from markus-lassfolk's set: pause mowing when it rains, low
  battery alert, job complete notification.
- **HA backups**: everything yarbie keeps lives under `/config/yarbie/` or `.storage`, so
  it is included automatically.

## Yard editor panel

Mockups: `mockups/03-editor-changes.png`, `mockups/04-plans-route-preview.png`,
`mockups/05-area-settings-wear.png`, `mockups/07-phone.png`.

**Registration:**
- `panel_custom`, admin only, sidebar title "Yard editor".
- The integration serves the module from `www/` with a content hash for caching.

**Frontend:**
- `main.ts` becomes a `<yarbie-panel>` custom element. HA passes it the `hass` object.
- `api.ts` keeps its typed functions but calls `hass.callWS` instead of `fetch`.
- Robot status comes from `hass.states`, pushed live. The 4-second status poll goes away.
- It uses HA theme variables, so it follows light and dark mode.
- On a phone (narrow screens) it switches to floating tool buttons and a bottom sheet.

**Map:**
- MapLibre with satellite imagery: Google if a key is set, otherwise the free sources
  already built in.
- Imagery alignment offset, as today.

**Tools:** select, no-go zone, pathway, sidewalk, reshape area, undo, route, wear, backups.

**Drawer tabs:**
- **Changes**:
  - The diff list, with the reason for any change that can't be sent.
  - The dock-link (WP005) warning.
  - Discard, and the two-click "Confirm: send N changes".
- **Selected**: name, on/off state and geometry details for the selected feature.
- **Plans**: list, create, edit and delete plans, start a plan, and route preview.
- **Areas**: name, mowing pattern, cutting height, edge passes and speed (the verified
  mower area params), plus the wear summary.
- **Runs**: see TODO 1.

### Websocket API

Every command checks for an admin user. Every write records `connection.user.name` as the
actor.

| Command | Replaces |
|---|---|
| `yarbie/map` | `GET /api/map` |
| `yarbie/preview` | `POST /api/map/preview` |
| `yarbie/apply` `{features, revision}` | `POST /api/map/apply` |
| `yarbie/backups/list`, `yarbie/backups/get` | `/api/backups*` |
| `yarbie/imagery` | `/api/imagery` |
| `yarbie/plans/list|save|delete` | `/api/plans*` |
| `yarbie/area_params/get|save` | `/api/areas/{id}/mower-params` |
| `yarbie/progress/subscribe` | `/api/progress` (it is pushed now) |
| `yarbie/route/start`, `yarbie/job/subscribe` | `/api/plans/{id}/route` |
| `yarbie/heatmap/start` | `/api/heatmap` |
| `yarbie/export` | `/api/export.geojson` |

`/api/status` and `/api/client-log` go away: status comes from entities, and panel errors
go to HA's system log.

### Long-running robot calls are jobs

`preview_plan_path` makes the robot compute the whole route. A big map has taken over a
minute, and then refused anyway. A held-open call is fragile: the phone sleeps, the
network switches, the page reloads. When that happens the user can't tell whether the
robot is still working, and clicking again would send a second command that takes the
controller.

How the job version works:
- `yarbie/route/start` returns a job id at once. The integration runs **at most one**
  preview per (plan id, map revision) and keeps the result.
- The panel subscribes to the job. After a reconnect or reload it picks up the running
  job or the stored result. A second click joins the existing job.
- While it runs, the activity sensor reads "computing route", and map writes wait for it.
- yarbo-local has no verified command to cancel the robot's calculation. So the panel
  offers **"Stop waiting"**, not "Cancel".
- The plan-history read for the wear heatmap uses the same mechanism.

## Safety rails (all kept, all inside the integration)

- A backup of the full robot map is written before every apply, to
  `/config/yarbie/backups/<stamp>-<actor>-before-apply.json`.
- **Revision check:** an apply is refused if the map changed since the panel loaded it.
- **Busy check:** writes are refused while a plan is running or paused.
- One write at a time (a lock around apply, plan and settings writes).
- Two-click confirm in the panel.
- Dock-link (WP005) warning in the change list.
- Only commands verified in yarbo-local's registry. There is no raw command path.
- Writes are off when the firmware is untested, and when "Allow map writes" is off.

## Storage

| What | Where |
|---|---|
| Map backups | `/config/yarbie/backups/` (included in HA backups) |
| Run log (TODO 1) | `/config/yarbie/runs/` |
| Robot address, Google tile session, imagery offset | HA `Store` (`.storage/yarbie.*`) |
| Settings | Config entry and its options |

## Migration from markus-lassfolk's integration

The robot's broker has no authentication and each client gets its own ID, so both
integrations can **read** at the same time. Only one may command the robot.

1. Build and test against the simulator. No robot is involved.
2. Install yarbie with **Observe only** on, next to the existing integration. Compare the
   sensors, tracker and events over a few days.
3. **Cut over:**
   - Disable (don't delete) markus-lassfolk's integration, then turn off Observe only.
   - Point the automations, blueprints and dashboards at yarbie's entities, renaming
     entity IDs to the old ones where that saves editing.
4. **Robot checks from the panel:**
   - The first-time checks: draw a small no-go zone, send it, check it in the Yarbo app;
     draw one in the app, reload, check it appears; delete the test zone.
   - Controls round trip: beep, lights, start/pause/dock.
   - An edit made while the phone app is open.
   - Restart HA during a route preview.
   - Restore an HA backup and confirm the yarbie backups return.
5. Remove markus-lassfolk's integration and `python-yarbo`.

## Build plan

Each phase ends with `pytest`, `ty check`, and the frontend typecheck passing.

0. **Baseline.**
   - Commit the current tree (the repo has no commits yet).
   - Delete `docs/remote-design.md`.
1. **Library.**
   - Fork yarbo-local and vendor it with subtree at `395bdb5`.
   - Add the CI registry and firmware check.
2. **Skeleton.**
   - Manifest, config flow (DHCP, manual, scan, simulator), coordinator.
   - Move `mapdoc`, `diff`, `heatmap` and `demo` into `editor/`.
   - Test harness: `pytest-homeassistant-custom-component` with the simulator.
3. **Entities and actions** to parity (the tables above), plus diagnostics, the Repairs
   issues and the kill switch.
4. **Editor backend.**
   - `apply.py`, `backups.py`, the websocket API, and jobs.
   - Port `tests/test_api.py` to websocket tests, including duplicate route-preview
     clicks, reconnecting during a job, a stale revision, and refusing writes while busy.
5. **Panel.**
   - Wrap `main.ts` as `<yarbie-panel>`, move `api.ts` onto `hass.callWS`, take status
     from `hass.states`, add the phone layout.
   - Build into `www/`.
   - Dev loop: a local HA container on the Mac with the integration mounted, plus
     `vite build --watch`.
6. **Extras:** logbook and notifications, the `image` entity, blueprints, the HACS
   manifest.
7. **Migration** (the section above).
8. **Clean up.** Remove `backend/`, the Docker files and the server dependencies, and
   rewrite the README around the integration. Done, ahead of phase 7.

## TODO

Outline smoothing (fewer points, rounder curves, areas only shrink and no-go zones only
grow) has its own design: `area-smoothing.md`.

### 1. Run history page

A **Runs** tab in the panel, and a dashboard-friendly summary.

**Data sources:**
- **The robot's own history** (`read_all_plan_history`, verified): plan, start, duration,
  areas finished (`finishIds`), charge trips. The robot keeps the history, so the tab
  works from day one.
- **yarbie's run log**, written by the coordinator from lifecycle events into
  `/config/yarbie/runs/<start>.json`:
  - start and end, pauses and why, errors and fault codes, obstacles
  - battery at start and end, trips home, rain during the run
  - which map revision it ran on
  - who started it (HA user or automation)

**The list:** one row per run, filterable by plan and date range. Each row shows:
- date, plan and outcome (finished, stopped, failed with code)
- duration, areas done
- battery used, charge trips

**A selected run:**
- Its areas shaded by done or left, on the map as it was then.
- A timeline of the run's events: start, pauses, errors, obstacles, trips home, finish.
- Links to the backup and map revision it ran on.

**Entities:** "last run" sensors (outcome, duration, finished time) so dashboards and
automations can use it without the panel.

**Retention:** keep 1 year of run logs by default, set in options.

### 2. Blade-load heatmap (investigate first; not committed)

The idea: map blade motor load across the yard to estimate how much the grass grew
between mows, by area or by cell. It isn't clear yet whether the result would be worth
the effort, so step 1 is only to check whether the data is good enough.

**What exists in the field map** (Lawn Mower Pro, firmware 3.14.11):

| Field | Status |
|---|---|
| `mower_head_info03/04.left_/right_blade_motor_rpm` (about ±2800 cutting) | verified |
| `…_blade_motor_speed`, commanded (±80 %) | verified |
| `…_blade_motor_current`, community mapping, scale 0.01 A | **not verified**. Pushed (227 times in captures), but scale, and whether it is motor or supply current, are unknown |
| `CombinedOdom.x/y/phi`, map-frame position | verified |
| `WheelSpeedMSG.left/right` | verified |
| `BatteryMSG.voltage` | verified |
| `middle_blade_motor_*` | ignore: no head tag, never pushed, junk sample |

These fields are tagged for heads `mower` (3) and `mower_pro` (5). Every capture is from
a Pro.

**Step 1: check the data.**
- Record one full mow with `yarbo-local sniff`.
- Confirm that blade current is non-zero while cutting, that its scale is sane, and how
  often it is pushed. At about 0.4 m/s, 1 Hz gives 2–3 samples per 1 m cell per pass.
- If current proves unreliable, the fallback signal is blade rpm sag against the
  commanded speed.

**If it is worth building:**
- **Recording:** during cutting, sample time, position, blade currents and rpm, wheel
  speed and battery voltage into the run log (TODO 1). Not as HA states, because of the
  recorder load on the eMMC.
- **Per cell:**
  - Energy per m² (≈ |I| × V × dt, put into the same 1 m grid as the wear heatmap).
    Energy rather than power, because the robot slows down in thick grass.
  - Divide each run by its own median, to cancel blade sharpness and general moisture.
  - Divide by the days since that cell was last cut (from the previous run's coverage)
    to get relative growth per day.
- **Things it can't separate:** wet grass (partly flagged by the rain sensor),
  cut-height changes, slopes, turns. Flag or skip runs with rain or a changed cut height.
- **UI:** "Blade load" and "Growth since last cut" layers in the Wear tool, plus
  once-per-run sensors per area ("Back lawn load vs median") for automations such as
  mowing an area early.

### 3. Move plan editing out of the map editor

Plans are a robot setting, not map geometry: which areas, in what order. Today they are
edited in the Yard editor's Plans section. Move editing to where the robot's other
controls live, and keep only what needs the map in the editor (route preview, picking
areas by clicking them).

**Where it goes:**
- **A plan card on the Yarbo dashboard** (the strategy): the plans with their areas,
  New, Edit (name and areas as a checklist, then the order), Delete with a confirm, and
  Start.
- **The device page**, as far as HA allows: the plan select stays. Add a "Manage plans"
  link to the dashboard card, because the device page cannot host a custom editor.

**How:**
- **A custom Lovelace card** (`custom:yarbie-plans-card`), served beside the dashboard
  strategy and placed in its first column. It uses the existing `yarbie/plans/*`
  commands, so the backend does not change.
- Writes keep the same rails: the write lock, the busy check, the kill switch, untested
  firmware.
- The Yard editor's Plans section shrinks to a list with **Route** and **Edit on the
  dashboard**. When a plan's route is shown, its areas are highlighted on the map.
- **Optional:** with the editor open, clicking areas on the map adds them to the plan
  being edited, which the card would offer as "Pick on the map".

### 4. Schedule editor

yarbo-local has verified `read_schedules`, `save_schedule` and `del_schedule`. But only a
*disabled* schedule has ever been stored on a real robot. What `schedule_type`,
`week_day` (a single day or a bitmask?) and `return_method` mean when a schedule fires is
not known. The app sent 3, 1 and 2.

**Step 1: learn the fields.**
- In the Yarbo app, make a few enabled schedules: one weekday; several days; different
  "return" choices; "resume from last progress" on and off.
- Capture each with `yarbo-local sniff`.
- Let one fire, and record what the robot did and what `plan_feedback` and the run
  history show.
- Add what was learnt to the fork's `protocol/` (commands and codes), then pull it in.

**Then build:**
- **Entities:**
  - A `calendar` entity showing upcoming scheduled runs, so they appear in HA's
    calendar and automations can see them.
  - A `switch` per schedule to enable or disable it.
- **A schedule card** on the Yarbo dashboard: list, add, edit (plan, days, start and end
  time, return method, resume progress), enable/disable, delete. It sits next to the
  plan card from TODO 3.
- **Websocket commands** `yarbie/schedules/list|save|delete`, under the same write rails.
- **Run history:** a run started by a schedule says so ("started by schedule
  <name>"). The run log can match the start against the schedule's time.
- **Rain:** whether to skip a scheduled run when it is raining, as an option, which needs
  the rain-pause blueprint's logic at start time.

## Appendix: delta against markus-lassfolk's integration (mower only)

- **Same:**
  - battery, activity and head sensors; charging and problem binary sensors
  - buttons: beep, return to dock, pause, resume, stop
  - all-lights group, the `lawn_mower` entity, job lifecycle event, GPS tracker
  - RTK status, heading, satellites, rain, planning, emergency stop, the 7 light channels
  - `start_plan`, `set_lights`, pause/resume/return to dock
  - diagnostics, DHCP discovery, three blueprints
- **Changed:**
  - Charging power becomes wireless charger output voltage and current.
  - The buzzer switch becomes the sound switch (`set_sound_param`).
  - Rain is on by default.
- **Dropped:**
  - `send_command` (raw MQTT).
  - Snow and leaf head entities, `set_chute_velocity`, the snow deployment blueprint.
- **Added:**
  - **Entities:**
    - plan select, plan progress and time remaining sensors
    - error code sensor, RTK fix binary sensor
    - person detection and child lock switches
    - wake and refresh map buttons
    - map image entity
    - fault binary sensors (off by default)
  - **Actions:** `get_map`.
  - **Rails:** the untested-firmware Repairs issue and the kill switch.
  - **The Yard editor panel:** map edits with backups, plans, area settings, route
    preview, wear heatmap.
  - **Records:** logbook entries and notifications for map changes.
  - **Run history** (TODO 1).

## Open items

- yarbo-local caches controller ownership. Check that the session notices when the phone
  app takes the controller, and take it again before each write batch if it doesn't.
- Before cutover, list the markus-lassfolk entity IDs used in automations and dashboards,
  so they can be renamed to match.
