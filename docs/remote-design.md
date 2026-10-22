# yarbie remote: edge on Home Assistant Green, everything else on GCP

Status: proposal · 2026-10-22

## Goal

Open yarbie from anywhere at `https://yarbie.lauricha.com`, behind a Google login, and
have it always answer, even when the house is offline. The only part that has to be at
home is the part that talks to the robot's MQTT broker on port 1883. That part runs on the
Home Assistant Green.

What stays the same:

- Every write still goes through yarbo-local's *verified* commands.
- The safety rails stay as they are: backup before apply, revision check, busy check,
  confirm click, dock-link warning. They also keep running **next to the robot**, so a WAN
  drop in the middle of an apply cannot leave a write half-checked.
- `sim` mode on a laptop still runs as one process with no cloud. Tests still run against
  the simulator.

## Where each part runs

```
 Browser ──HTTPS──▶ yarbie.lauricha.com  (Cloud Run "yarbie-cloud", us-central1)
                     │  Google OIDC login, email allowlist, session cookie
                     │  UI (static), /api/*, imagery, diff preview, heatmap, route/progress → GeoJSON
                     │  cached snapshot + status (Firestore), backups mirror (GCS)
                     │
                     │ commands  ┌────────────────────┐
                     ├──publish─▶│ Pub/Sub yarbie-cmd │◀──streaming pull (outbound gRPC)──┐
                     │           └────────────────────┘                                    │
                     │◀──HTTPS POST /edge/reply, /edge/telemetry, /edge/backup ───────────┤
                     │   (Google ID token of the edge service account)                    │
                                                                                         │
 Home LAN ─────────────────────────────────────────────────────────────────────────────── │ ──
   Home Assistant Green (aarch64, HA OS)                                                  │
     add-on "yarbie-edge"  ─────────────────────────────────────────────────────────────────┘
       RobotSource (yarbo-local) ──MQTT :1883──▶ Yarbo robot
       EdgeService: apply lock, revision/busy checks, local backups, command dedupe/deadlines
       Ingress panel: today's full yarbie UI for LAN use, behind HA's own login
```

The edge only makes outbound connections: a streaming pull from Pub/Sub, and HTTPS to
Cloud Run. Nothing at home is port-forwarded.

### Why this split

| Concern | Where it runs | Reason |
|---|---|---|
| MQTT to the robot, discovery | edge | It has to be on the LAN |
| `apply` (lock, revision, busy, backup, send ops) | edge | It is one transaction. Splitting it across the WAN would weaken every rail |
| Plan/param writes | edge | Same busy check, same lock |
| Local backup before apply | edge | It must succeed even when the cloud cannot be reached |
| Raw map → GeoJSON (`mapdoc`), `diff.compute` for preview | cloud | Pure functions on the cached snapshot, so edits can be drafted while the house is offline |
| Heatmap | cloud | CPU-bound and pure. Only `plan_history` comes from the robot |
| Imagery (Google key, session) | cloud | The key never needs to be at home |
| Auth, audit log, cached state, backup mirror | cloud | Always on |

### Why Cloud Run + Pub/Sub (and not a WebSocket or a VM)

A persistent edge→Cloud Run WebSocket keeps a request open around the clock, and Cloud
Run bills that as an always-active instance (roughly $45–60/month at 1 vCPU). A free-tier
e2-micro VM would be cheap, but it is a server to patch. With Pub/Sub:

- Cloud Run only bills per request and can scale to zero. Telemetry posts every 5–30 s are
  short, so they fit inside the free tier and also keep an instance warm.
- The edge's Pub/Sub streaming pull goes to Google, not to Cloud Run, so it costs nothing
  to hold open.
- Expected total: **under about $2/month**, plus Google Map Tiles use.

Pub/Sub delivers at least once and can redeliver late, so the edge **dedupes by command id
and drops commands past their deadline** (see the protocol section). Without this, a
redelivered `save_plan` could create a duplicate plan, or a write could run minutes after
the user gave up on it.

## Security model

**Users (browser → cloud)**
- Google OIDC (Authorization Code + PKCE) handled in the app with Authlib's Starlette
  client. The allowlist is `YARBIE_ALLOWED_EMAILS`, e.g. `josh@lauricha.com`. If
  lauricha.com is a Workspace domain, set the OAuth consent screen to *Internal* and Google
  enforces the domain as well.
- The session is a signed cookie: `HttpOnly; Secure; SameSite=Lax`, with a 7-day lifetime
  and the signing key in Secret Manager.
- CSRF: every non-GET `/api/*` request must carry `X-Yarbie: 1`. `api.ts` adds it, and
  together with SameSite=Lax that is enough for a same-origin SPA.
- Every route except `/auth/*`, `/healthz` and `/edge/*` requires a session. An expired
  session returns 401, and the page then redirects to `/auth/login`.
- Why not IAP? IAP checks auth before any app code runs, which is better, but it needs an
  external HTTPS load balancer (about $18/month) to sit on a custom domain. That is the
  upgrade path if the in-app login ever seems too thin. Nothing else in this design changes
  if you add it.

**Edge (HA Green → GCP)**
- The edge runs as service account `yarbie-edge@`, with a key stored in the add-on's
  options (`/data/options.json`, readable only by the add-on). It has two roles:
  `pubsub.subscriber` on the `yarbie-edge` subscription only, and permission to mint ID
  tokens for itself.
- `/edge/*` endpoints verify a Google-signed ID token. The audience must be the service URL
  and the email must be `yarbie-edge@…`. No shared secret is needed.
- The cloud **cannot send raw MQTT**. The edge serves a fixed list of methods (below) and
  never exposes `raw_request`. The rails run on the edge whatever the cloud asks.
- Add-on option `allow_remote_writes` (default `true`): when off, the edge refuses every
  write that arrives through the cloud. This is a kill switch you can flip from HA.

**Other**
- Restrict the Google Maps key by HTTP referrer to `https://yarbie.lauricha.com/*`. The
  tile URL already exposes it to the browser.
- Audit: every write carries `actor` (the session email). The cloud logs it to Cloud
  Logging, and the edge writes it into the backup's name and metadata.
- Backups are mirrored to a GCS bucket with versioning and a 1-year lifecycle rule. The
  edge's local copy stays authoritative.

## Code shape

There is still one Python package, with extras: `yarbie[edge]` adds `google-cloud-pubsub`
and `google-auth`, and `yarbie[cloud]` adds `authlib`, `itsdangerous`,
`google-cloud-firestore`, `google-cloud-storage` and `google-cloud-pubsub`.

```
backend/yarbie/
  core/        mapdoc.py diff.py heatmap.py demo.py        (pure, moved unchanged)
  sources/     base.py robot.py cloud.py                    (unchanged)
  gateway.py   RobotGateway protocol + LocalGateway
  edge/        service.py   EdgeService: apply/_send, lock, backups (today's MapService write half)
               store.py     BackupStore (+ actor, + upload hook)
               discovery.py
               link.py      Pub/Sub pull loop, method table, dedupe, deadlines, telemetry, reply POST
               main.py      `yarbie-edge`: link + optional ingress UI (all-in-one app)
  cloud/       remote.py    RemoteGateway: publish command, await reply future, cache reads
               auth.py      OIDC login/logout/callback, session, allowlist, CSRF
               edge_api.py  /edge/reply /edge/telemetry /edge/snapshot /edge/backup (ID-token checked)
               cache.py     Firestore status/snapshot docs, GCS backups
               main.py      `yarbie-cloud`
  protocol.py  pydantic Command/Reply/Telemetry models, method names, error codes
  api.py       /api/* routes, now on RobotGateway instead of MapService
  imagery.py   unchanged
  main.py      `yarbie`: today's all-in-one (sim/lan/cloud), LocalGateway
```

### The seam: `RobotGateway`

`api.py` stops depending on `MapService` and `RobotSource` and depends on this instead:

```python
class RobotGateway(Protocol):
    name: str
    async def status(self) -> GatewayStatus              # RobotStatus + writable, online, age_s
    async def snapshot(self) -> MapSnapshot              # raw, reference, revision
    async def plans(self) -> list[dict[str, Any]]
    async def plan_feedback(self) -> dict[str, Any] | None   # raw get_plan_feedback payload
    async def preview_route(self, plan_id: int) -> dict[str, Any] | None
    async def plan_history(self) -> list[dict[str, Any]]
    async def mower_area_params(self, area_id: int) -> dict[str, Any] | None
    async def apply(self, edited: dict[str, Any], revision: str, actor: str) -> dict[str, Any]
    async def save_plan(self, name: str, area_ids: list[int], plan_id: int | None, actor: str) -> int
    async def delete_plan(self, plan_id: int, actor: str) -> None
    async def update_mower_area_params(self, area_id: int, changes: dict[str, Any], actor: str) -> dict[str, Any]
    async def backups(self) -> list[dict[str, Any]]
    async def backup(self, name: str) -> dict[str, Any]
```

- **`LocalGateway`** wraps `EdgeService` and `RobotSource` in-process. The all-in-one
  `yarbie`, the edge's ingress UI, the edge link's method table, and every existing test
  all use it.
- **`RemoteGateway`** (cloud) serves `status`/`snapshot`/`backups` from the cache. The rest
  becomes commands. Reads made through it have the same shape as local reads, so
  `/api/progress` and `/api/plans/{id}/route` keep their lon/lat conversion code
  unchanged.
- Plan feedback crosses the wire as the robot's raw payload, and the cloud parses it with
  `PlanFeedback.from_wire`. So nothing new needs serialising, and the `finishIds` record
  workaround keeps working.

### Wire protocol (`protocol.py`)

```jsonc
// cloud → Pub/Sub topic yarbie-cmd (attributes: method, id)
{"id": "01J…ulid", "method": "apply", "deadline": 1761170000.0, "actor": "josh@lauricha.com",
 "params": {"edited": {...}, "revision": "ab12…"}}
// edge → POST /edge/reply
{"id": "01J…ulid", "ok": false, "error": {"code": "conflict", "message": "The map on the robot changed…"}}
// edge → POST /edge/telemetry (every 5 s while a plan runs, 30 s idle, and on change)
{"status": {...RobotStatus...}, "revision": "ab12…", "edge_version": "0.2.0", "allow_remote_writes": true}
// edge → POST /edge/snapshot   when the revision changes, and after every apply
{"raw": {...}, "reference": [lat, lon], "revision": "ab12…"}
```

- Methods: `snapshot, plans, plan_feedback, preview_route, plan_history,
  mower_area_params, apply, save_plan, delete_plan, update_mower_area_params`. Every other
  method is rejected as `unknown_method`.
- Error codes map to today's `_fail` statuses: `conflict` (409), `read_only` (403),
  `invalid` (400), `robot` (502), and `expired`/`edge_offline` (504/503).
- Deadlines: reads get 15 s, writes 30 s, `preview_route` 150 s. The edge **acks and drops**
  any command already past its deadline, and also checks the deadline again just before
  sending to the robot. The cloud waits until the deadline plus 2 s, then answers 504.
- Dedupe: the edge keeps the ids it has seen for the last 10 minutes (more than Pub/Sub's
  ack deadline plus retry window) and replies again with the cached result instead of
  running the command a second time.
- The subscription has a 10-minute retention and exactly-once delivery turned on. That
  shrinks redelivery but does not remove it, so the dedupe stays.

### Offline behaviour

The cloud marks the edge `online` while its last telemetry is under 75 s old. When it is
offline:

- `/api/status` reports `connected: false, writable: false, stale_s: N`.
- `/api/map` serves the cached snapshot, and preview still works against it.
- Applies and plan writes get a 503 at once (no command is published).
- Optional later step: fall back to the existing read-only Yarbo-cloud source
  (`sources/cloud.py`) for live status.

## Frontend changes (small)

- `api.ts`: send `X-Yarbie: 1` on every request, and on a 401 go to `/auth/login?next=…`.
- Status pill: add an "edge offline · cached N min ago" state.
- Header: show the signed-in email and a sign-out link.
- Leave the 4 s status poll alone: the cloud answers it from memory or Firestore without
  touching the edge.

## Home Assistant Green add-on

- The image is multi-arch (`linux/arm64` + `amd64`), built in CI (GitHub Actions or Cloud
  Build) and pushed to GHCR or Artifact Registry. It reuses the current Dockerfile with
  `CMD yarbie-edge`.
- The add-on repo (`ha-addon/yarbie-edge/config.yaml`):
  - `host_network: true`, so the /24 robot scan sees the real LAN
  - `ingress: true` with `ingress_port: 8765` for the local UI
  - `map: [share]` is not needed. The add-on's `/data` holds backups, `lan_robot.json` and
    `options.json`
  - options: `yarbo_host`, `yarbo_serial`, `scan`, `cloud_url`, `pubsub_subscription`,
    `service_account_json` (a password-type field), `allow_remote_writes`
- A small `run.sh` turns `options.json` into the `YARBIE_*` environment.
- HA's Mosquitto add-on does not conflict: yarbie connects *out* to the robot's broker and
  never listens on 1883.
- The ingress UI is the all-in-one app over `LocalGateway`, so with the internet down you
  can still edit from home through HA. Both paths share the same `EdgeService` lock and
  revision check.

## GCP resources (Terraform, `infra/`)

- APIs: run, pubsub, firestore, storage, secretmanager, iamcredentials, artifactregistry.
- Cloud Run `yarbie-cloud`: region `us-central1`, `min=0 max=1` instances, concurrency 80,
  timeout 300 s, about 512 MiB.
  - `max=1` keeps reply futures in one process. Move replies to Firestore if you ever need
    more instances.
- Domain mapping `yarbie.lauricha.com`, plus a CNAME to `ghs.googlehosted.com` wherever
  lauricha.com's DNS lives.
  - Don't use Firebase Hosting rewrites: their 60 s limit is too short for `preview_route`.
- Pub/Sub topic `yarbie-cmd` and subscription `yarbie-edge`: exactly-once delivery, 10 min
  retention, 60 s ack deadline.
- Firestore (native mode) with docs `edge/status` and `edge/snapshot`. Large snapshots go
  to GCS instead (Firestore's limit is 1 MiB per doc).
- GCS bucket `<project>-yarbie-backups`: versioning on, uniform access, 365-day lifecycle.
- Secret Manager: `oauth-client-secret`, `session-key`, `google-maps-key`.
- Service accounts:
  - `yarbie-cloud@` (runtime): publisher on the topic, Firestore user, object admin on the
    bucket, secret accessor
  - `yarbie-edge@`: subscriber on the subscription
- OAuth client: create it by hand in the console (Terraform can't fully manage the consent
  screen), with redirect URI `https://yarbie.lauricha.com/auth/callback`.

## Plan

Each phase ends with `pytest`, `ty check backend tests` and `npm run typecheck` green.

0. **Baseline.** The repo has no commits yet, so commit the current tree first. Every
   later step is then a reviewable diff.
1. **Introduce the seam, no behaviour change.**
   - Move `mapdoc/diff/heatmap/demo` into `core/`.
   - Split `MapService` into the read helpers and `EdgeService` (apply, lock, backups).
   - Add `RobotGateway` and `LocalGateway`, and point `api.py` at the gateway.
   - Existing tests pass untouched, apart from imports.
2. **Protocol + edge link, in-process.**
   - Add `protocol.py`, plus `edge/link.py` with an injectable transport.
   - Add `RemoteGateway`.
   - New test: run the whole `test_api.py` suite through
     `RemoteGateway → in-memory bus → edge link → LocalGateway(sim)`. Add cases for an
     expired command, a duplicate delivery of `save_plan`, the edge offline during apply,
     and an unknown method.
3. **Cloud app.**
   - Add `auth.py`: OIDC, allowlist, session, CSRF. Test with a stubbed token endpoint.
   - Add `edge_api.py` with ID-token checks; tests use a locally signed token and a JWKS
     override.
   - Add `cache.py`, with Firestore/GCS behind a small interface plus an in-memory version
     for tests.
   - Run the Pub/Sub emulator in one integration test.
4. **Frontend:** the CSRF header, the 401 redirect, the offline/stale state, and the
   signed-in user.
5. **Edge packaging.**
   - Build the multi-arch image in CI and add the add-on repo.
   - Install it on the Green, run `yarbie discover` from the add-on shell, and check the
     ingress UI.
6. **Infra.**
   - Apply Terraform, create the OAuth client, deploy Cloud Run, add the DNS CNAME, wait
     for the managed cert.
   - Restrict the Maps key by referrer.
7. **Cutover checks on the real robot.**
   - Redo the README's "first time on a real robot" steps from a phone on LTE.
   - Pull the Green's network cable during an apply. The apply must either finish locally
     and show up after reconnect, or never start.
   - Confirm that a command expired in the queue is dropped.
   - Flip `allow_remote_writes` off and confirm remote writes get a 403.
   - Confirm the backup is in GCS with the actor recorded.
8. **Docs:** update the README, which currently says "listens on loopback only, no login".

## Open questions

1. Is lauricha.com a Google Workspace domain? If so, use the *Internal* consent screen
   and skip app verification.
2. Where is lauricha.com's DNS hosted? You need to add one CNAME there.
3. Is about $18/month acceptable for IAP plus a load balancer instead of the in-app login?
   That is the only real fork in this design.
4. Which GCP project and region? `us-central1` is assumed because Cloud Run domain
   mappings work there.
