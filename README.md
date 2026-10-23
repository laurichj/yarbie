# yarbie

A Home Assistant integration for Yarbo robots, with a map editor in the sidebar.

- **The robot in Home Assistant**: a lawn mower entity (start the chosen plan, pause,
  dock), plan select, battery, activity, run progress and time left, faults, rain, the
  stop button, RTK, lights, voice prompts, person detection, child lock, its position,
  plan start/pause/finish events, and the map as an image.
- **The Yard editor**: the robot's map over satellite imagery. Reshape and rename work
  areas, draw no-go zones, pathways and sidewalks, edit plans and per-area mowing settings,
  and preview the route the robot would take.

It talks to the robot's own MQTT broker on your network through
[yarbo-local](https://github.com/yarbo-local/yarbo-local) and nothing else: no Yarbo
account, no cloud. Design notes are in [docs/ha-design.md](docs/ha-design.md).

## What it can change

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
- Every apply first saves the robot's full map to `/config/yarbie/backups/`, named with
  who sent it. HA's own backups include that folder.
- An apply is refused if the map changed since you loaded it, for example from the app.
- Map, plan and settings writes are refused while a plan is running or paused.
- Apply needs a second click to confirm.
- The change list warns when a pathway loses its link to the dock. Without one, plans that
  start from the dock fail with WP005.
- If the robot reports firmware the command list was not verified on, writes turn off and
  Settings, Repairs asks you to check by hand before turning them back on. Controls
  (start, pause, dock, lights) keep working.
- Options: **Allow map writes** (a kill switch) and **Observe only** (send the robot
  nothing; for running beside another integration).
- The editor is an admin-only panel, behind Home Assistant's login. There is no raw
  command action.

## Install

Home Assistant 2026.10 or newer, able to reach the robot on TCP port 1883.

1. HACS, Integrations, three dots, Custom repositories: add `https://github.com/laurichj/yarbie` as an
   Integration, install **yarbie**, restart Home Assistant. (Or copy
   `custom_components/yarbie` into your config's `custom_components`.)
2. Settings, Devices & services, Add integration, **yarbie**. Leave the address empty to
   scan the network, or type the robot's address. Home Assistant also offers the robot
   when its router hands it an address (DHCP discovery).
3. **Yard editor** appears in the sidebar for admins.
4. For a dashboard: Settings, Dashboards, Add dashboard, **Yarbo** (under Community
   dashboards). It lays out the robot, its controls, the map and recent activity in
   three columns, and builds itself from the robot's entities each time it opens, so
   renaming them needs no editing. With two robots it gets a view each.

The robot's address is kept and checked at each start; if the robot has moved, yarbie
scans for it by serial. Set a subnet in the options if the robot is on another network.

Blueprints for rain, low battery and finished plans are in `blueprints/automation/yarbie`.

## First time on a real robot

Do this once (and again after a firmware update) before trusting writes:

1. Open the Yard editor and check the outlines sit on your lawn. If the photo is off, use
   **Imagery alignment** (display only).
2. Draw a small no-go zone, apply it, and confirm the app shows it and can edit and
   delete it.
3. Draw one in the app, **Reload** the editor, and confirm it appears.
4. Delete the test zone from the editor.

## Develop

```sh
python3 -m venv .venv && .venv/bin/pip3 install -e '.[dev]'
(cd frontend && npm ci && npm run build)   # builds the panel into custom_components/yarbie/www
scripts/dev_ha.sh                          # a throwaway HA on http://127.0.0.1:8123
```

In the dev HA, add yarbie with the address `simulator` for a built-in demo yard. `cd
frontend && npm run watch` rebuilds the panel on save; restart HA to load it.

Checks:

```sh
.venv/bin/pytest                         # against yarbo-local's simulator
.venv/bin/ty check custom_components tests
(cd frontend && npm run typecheck)
python3 scripts/vendor_yarbo_local.py --check
```

### yarbo-local

`third_party/yarbo-local` is a `git subtree` of the library, from our fork
([laurichj/yarbo-local](https://github.com/laurichj/yarbo-local), remote `yarbo-local`),
pinned to the commit the protocol was verified with.
`custom_components/yarbie/_vendor/yarbo_local` is a copy made from it by
`scripts/vendor_yarbo_local.py`, never edited by hand. Fixes go to the fork first.
To update (after syncing the fork with upstream, or committing a fix there):

```sh
git subtree pull --prefix=third_party/yarbo-local yarbo-local <commit> --squash
python3 scripts/vendor_yarbo_local.py
```

Then redo the first-time checks above on the robot.
