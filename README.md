# yarbie

A Home Assistant integration for Yarbo robots, with a map editor in the sidebar.

- **The robot in Home Assistant**: a lawn mower entity (start the chosen plan, pause,
  dock), plan select, battery, activity, run progress and time left, faults, rain, the
  stop button, RTK, lights, voice prompts, person detection, child lock, its position,
  plan start/pause/finish events, the map as an image, a switch per schedule and a
  schedules calendar.
- **Dashboard cards**, placed by the Yarbo dashboard: plans (create, edit areas and their
  order, delete, start), schedules, runs (with each run's timeline), and the yard on a
  satellite map showing the run in progress, the route a plan would take, a past run's
  areas, and an estimated wear map. **Area settings**, a view of its own, shows every
  area's mowing settings in one table, to set a value on many areas at once and see which
  ones differ, or one area's settings as a form.
- **The Yard editor**: the robot's map over satellite imagery, for editing it. Reshape,
  smooth and rename work areas, draw no-go zones, pathways and sidewalks, review the
  changes and apply them, and revert to a backup.

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
| Schedules | ✓† | ✓† | ✓ |

\* The robot maps a new area by driving round it, and there is no verified delete command.

† Only a disabled schedule has been stored on a real robot so far. What `week_day`,
`schedule_type` and `return_method` mean when one fires is not confirmed, so yarbie
shows and sends the robot's own numbers, starts new schedules with the app's (1, 3 and
2), and labels them unconfirmed. The calendar reads `week_day` as a bitmask with Monday
as 1, and says on every event that this is a guess. Captures from the Yarbo app are
needed to settle it ([docs/ha-design.md](docs/ha-design.md), TODO 4).

Safety rails:
- Every apply first saves the robot's full map to `/config/yarbie/backups/`, named with
  who sent it. A change in Area settings first saves every affected area's settings
  there too. HA's own backups include that folder.
- An apply is refused if the map changed since you loaded it, for example from the app.
- Map, plan, schedule and settings writes are refused while a plan is running or paused,
  and are sent one at a time.
- Apply, and sending from the Area settings table, need a second click to confirm.
- The change list warns when a pathway loses its link to the dock. Without one, plans that
  start from the dock fail with WP005.
- If the robot reports firmware the command list was not verified on, writes turn off and
  Settings, Repairs asks you to check by hand before turning them back on. Controls
  (start, pause, dock, lights) keep working.
- Options: **Allow map writes** (a kill switch) and **Observe only** (send the robot
  nothing; for running beside another integration).
- The editor is an admin-only panel, behind Home Assistant's login. Every card changes
  things only for admins; other users see area settings, runs and the yard map
  read-only, and start plans from the mower's controls. There is no raw command action.

## Install

Home Assistant 2026.10 or newer, able to reach the robot on TCP port 1883.

1. HACS, three dots, Custom repositories: add `https://github.com/laurichj/yarbie` as an
   Integration, install **yarbie**, restart Home Assistant. HACS installs the latest
   [release](https://github.com/laurichj/yarbie/releases), whose `yarbie.zip` holds the
   built panel. (Or unzip a release's `yarbie.zip` into your config's
   `custom_components/yarbie`. Copying the folder from a checkout gives no panel: the
   built frontend is not in git.)
2. Settings, Devices & services, Add integration, **yarbie**. Leave the address empty to
   scan the network, or type the robot's address. Home Assistant also offers the robot
   when its router hands it an address (DHCP discovery).
3. **Yard editor** appears in the sidebar for admins.
4. For a dashboard: Settings, Dashboards, Add dashboard, **Yarbo** (under Community
   dashboards). Its **Yard** view lays out the robot and its controls, the yard map, and
   recent runs in three columns; its **Area settings** view is the settings table. It
   builds itself from the robot's entities each time it opens, so renaming them needs no
   editing. With two robots each gets its own views. The cards (**Yarbo plans**,
   **Yarbo schedules**, **Yarbo runs**, **Yarbo yard**, **Yarbo area settings**) can be
   added to any dashboard too.

   Links can pick what the cards show: `?area=<id>` on the Area settings view opens that
   area, and `?run=<key>` (or `?run=last`) and `?plan=<id>` on the Yard view open a run or
   filter the runs. The **Last run** sensor's `run` attribute is the key, for a
   notification to link to the run that just ended.

The robot's address is kept and checked at each start; if the robot has moved, yarbie
scans for it by serial. Set a subnet in the options if the robot is on another network.

Blueprints for rain, low battery and finished plans are in `blueprints/automation/yarbie`.

### A particular version

HACS offers each release, not the branch. For an older or newer one: HACS, **yarbie**,
three dots, **Redownload**, pick the version, then restart Home Assistant. New releases
show up as an update in Settings, Updates.

## First time on a real robot

Do this once (and again after a firmware update) before trusting writes:

1. Open the Yard editor and check the outlines sit on your lawn. If the photo is off, use
   **Imagery alignment** (display only; the dashboard's yard map uses it too, in the same
   browser).
2. Draw a small no-go zone, apply it, and confirm the app shows it and can edit and
   delete it.
3. Draw one in the app, **Reload** the editor, and confirm it appears.
4. Delete the test zone from the editor.

## Develop

```sh
python3 -m venv .venv && .venv/bin/pip3 install -e '.[dev]'
(cd frontend && npm ci && npm run build)   # builds the panel and cards into custom_components/yarbie/www (not in git)
scripts/dev_ha.sh                          # a throwaway HA on http://127.0.0.1:8123
```

In the dev HA, add yarbie with the address `simulator` for a built-in demo yard. `cd
frontend && npm run watch` rebuilds the panel on save; restart HA to load it. Without a
build, HA runs without the panel. Each checkout or worktree gets its own `.venv-ha` and
`.ha-dev`; to run one beside another, give it its own port on its first run:
`HA_PORT=8124 scripts/dev_ha.sh`.

Checks: `scripts/check.sh` runs all of CI's but hassfest and the HACS action, or one
group of them (`python`, `frontend`, `zip`):

```sh
.venv/bin/pytest                         # against yarbo-local's simulator
.venv/bin/ty check custom_components tests
(cd frontend && npm run typecheck && npm run build)
python3 scripts/vendor_yarbo_local.py --check
python3 scripts/build_release_zip.py     # yarbie.zip; checks the two versions agree
```

`check.sh zip` then runs the tests against the unzipped `yarbie.zip`. It uses `.venv`
and `node_modules` as they are, while CI installs both from scratch, so a package only
your venv has can pass here and fail there.

To try a branch as a release would install it, download its CI run's zip
(`gh run download <run-id> -n yarbie.zip`) and unzip it into a test HA's
`config/custom_components/yarbie`.

The built panel in `custom_components/yarbie/www/` is ignored by git; never commit it.
Releases build it.

### CI

`.github/workflows/ci.yml` runs on every push and pull request, on a fresh checkout:

- the checks above;
- `scripts/check.sh zip`: the zip a release would publish, and the test suite against the
  unzipped copy (so a file missing from the zip fails);
- Home Assistant's `hassfest` and the HACS validation action.

Each run keeps that `yarbie.zip` as an artifact for 14 days (the run's page, Artifacts),
for trying a branch on a real Home Assistant before it is released.

### Releasing

1. Set the new version in `custom_components/yarbie/manifest.json` and `pyproject.toml`
   (both the same, `X.Y.Z`; CI fails if they differ), commit, and merge to `main`.
2. Tag that commit and push the tag:

   ```sh
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```

`.github/workflows/release.yml` then checks the tag equals the manifest's version, runs
CI, and publishes a GitHub release with `yarbie.zip` and notes listing the commits since
the previous tag. HACS offers it from then on. Run **Release** by hand (Actions, Release,
Run workflow) for a dry run: the same checks and zip, as an artifact, and no release.

To build the zip locally: `(cd frontend && npm ci && npm run build)`, then
`python3 scripts/build_release_zip.py`.

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
