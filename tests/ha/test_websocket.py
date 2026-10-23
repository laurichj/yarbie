"""The Yard editor's websocket API end to end against yarbo-local's simulator."""

from __future__ import annotations

import math
import random
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.yarbie.const import OPT_ALLOW_WRITES
from custom_components.yarbie.editor import demo
from homeassistant.core import HomeAssistant

from conftest import WS, square, state


async def _map(ws: WS) -> dict[str, Any]:
    reply = await ws("yarbie/map")
    assert reply["success"], reply
    return reply["result"]


async def test_info_and_map(ws: WS) -> None:
    info = (await ws("yarbie/info"))["result"]
    assert info["source"] == "sim" and info["writable"] and not info["busy"]
    body = await _map(ws)
    assert body["reference"] == list(demo.REFERENCE)
    families = {f["properties"]["family"] for f in body["features"]["features"]}
    assert {"areas", "nogozones", "pathways", "dock"} <= families


def _densify(ring: list[list[float]], step_m: float = 0.35, wobble_m: float = 0.02) -> list[list[float]]:
    """A ring as the robot would drive it: a point every ``step_m``, wobbling a little."""
    lat = ring[0][1]
    per_m = (1 / (111_320 * math.cos(math.radians(lat))), 1 / 111_320)
    rng = random.Random(3)
    out: list[list[float]] = []
    for (x1, y1), (x2, y2) in zip(ring, ring[1:], strict=False):
        length = math.hypot((x2 - x1) / per_m[0], (y2 - y1) / per_m[1])
        steps = max(1, round(length / step_m))
        for k in range(steps):
            t = k / steps
            wx, wy = rng.uniform(-wobble_m, wobble_m), rng.uniform(-wobble_m, wobble_m)
            out.append([round(x1 + t * (x2 - x1) + wx * per_m[0], 9), round(y1 + t * (y2 - y1) + wy * per_m[1], 9)])
    return [*out, out[0]]


async def test_smooth_proposes_without_sending(ws: WS) -> None:
    info = (await ws("yarbie/info"))["result"]
    assert info["smoothing"] == {
        "tolerance_m": 0.1524, "unit": "cm", "unit_m": 0.01, "limits": [1.3, 90.0]
    }
    body = await _map(ws)
    fc = body["features"]
    area = next(f for f in fc["features"] if f["properties"]["family"] == "areas")
    area["geometry"]["coordinates"] = [_densify(area["geometry"]["coordinates"][0])]
    before = len(area["geometry"]["coordinates"][0])

    reply = await ws("yarbie/smooth", features=fc, ids=[area["id"]])
    assert reply["success"], reply
    (result,) = reply["result"]["results"]
    assert result["id"] == area["id"]
    assert len(result["geometry"]["coordinates"][0]) < before / 4
    stats = result["stats"]
    assert stats["area_after"] <= stats["area_before"]
    assert stats["max_offset"] <= 0.1524 + 0.002
    # Nothing went to the robot.
    assert (await _map(ws))["revision"] == body["revision"]

    pathway = next(f for f in fc["features"] if f["properties"]["family"] == "pathways")
    reply = await ws("yarbie/smooth", features=fc, ids=[pathway["id"]])
    assert not reply["success"] and reply["error"]["code"] == "invalid"


async def test_create_edit_delete_nogozone(
    hass: HomeAssistant, ws: WS, entry: MockConfigEntry
) -> None:
    changed = async_capture_events(hass, "yarbie_map_changed")
    body = await _map(ws)
    fc = body["features"]
    lat, lon = demo.REFERENCE
    fc["features"].append(
        {
            "type": "Feature",
            "id": "new:1",
            "geometry": {"type": "Polygon", "coordinates": [square(lon - 0.0001, lat + 0.0001)]},
            "properties": {"family": "nogozones", "name": "Pond"},
        }
    )
    ops = (await ws("yarbie/preview", features=fc))["result"]["ops"]
    assert [(o["kind"], o["family"], o["supported"]) for o in ops] == [("create", "nogozones", True)]

    reply = await ws("yarbie/apply", features=fc, revision=body["revision"])
    assert reply["success"], reply
    result = reply["result"]
    assert result["error"] is None and len(result["applied"]) == 1
    backup = entry.runtime_data.service.backups.dir / result["backup"]
    assert backup.exists() and backup.name.endswith(".json")
    await hass.async_block_till_done()
    assert [e.data["changes"] for e in changed] == [1]
    assert changed[0].data["backup"] == result["backup"]
    pond = next(f for f in result["map"]["features"]["features"] if f["properties"]["name"] == "Pond")
    assert pond["properties"]["id"] >= 100

    # Rename it, then delete it.
    fc = result["map"]["features"]
    next(f for f in fc["features"] if f["id"] == pond["id"])["properties"]["name"] = "Koi pond"
    renamed = (await ws("yarbie/apply", features=fc, revision=result["map"]["revision"]))["result"]
    assert [o["changes"] for o in renamed["applied"]] == [["name"]]
    fc = renamed["map"]["features"]
    fc["features"] = [f for f in fc["features"] if f["id"] != pond["id"]]
    deleted = (await ws("yarbie/apply", features=fc, revision=renamed["map"]["revision"]))["result"]
    assert [o["kind"] for o in deleted["applied"]] == ["delete"]
    names = {f["properties"]["name"] for f in deleted["map"]["features"]["features"]}
    assert "Koi pond" not in names and "Flower bed" in names

    listed = (await ws("yarbie/backups/list"))["result"]
    assert len(listed) == 3
    got = (await ws("yarbie/backups/get", name=listed[-1]["name"]))["result"]
    assert got["features"]["features"]


async def test_stale_revision_is_refused(ws: WS) -> None:
    fc = (await _map(ws))["features"]
    reply = await ws("yarbie/apply", features=fc, revision="stale")
    assert not reply["success"] and reply["error"]["code"] == "conflict"


async def test_unsupported_ops_are_skipped_not_sent(ws: WS) -> None:
    body = await _map(ws)
    fc = body["features"]
    fc["features"] = [f for f in fc["features"] if f["id"] != "areas:2"]
    result = (await ws("yarbie/apply", features=fc, revision=body["revision"]))["result"]
    assert result["applied"] == []
    assert [o["kind"] for o in result["skipped"]] == ["delete"]
    after = await _map(ws)
    assert any(f["id"] == "areas:2" for f in after["features"]["features"])


async def test_area_rename(ws: WS) -> None:
    body = await _map(ws)
    fc = body["features"]
    next(f for f in fc["features"] if f["id"] == "areas:1")["properties"]["name"] = "Front"
    result = (await ws("yarbie/apply", features=fc, revision=body["revision"]))["result"]
    assert result["error"] is None
    assert any(f["properties"]["name"] == "Front" for f in result["map"]["features"]["features"])


async def test_plans_and_params(hass: HomeAssistant, ws: WS) -> None:
    plans = (await ws("yarbie/plans/list"))["result"]
    assert [p["name"] for p in plans] == ["Whole yard", "Front only"]
    created = (await ws("yarbie/plans/save", name="Back only", area_ids=[2]))["result"]
    assert any(p["id"] == created["id"] for p in (await ws("yarbie/plans/list"))["result"])
    await hass.async_block_till_done()
    assert "Back only" in state(hass, "select.yarbo_plan").attributes["options"]

    params = (await ws("yarbie/area_params/get", area_id=1))["result"]
    assert params["params"]["first_clean_params"]["blade_height"] == 50
    saved = await ws(
        "yarbie/area_params/save", area_id=1, changes={"first_clean_params": {"blade_height": 60}}
    )
    assert saved["success"], saved
    again = (await ws("yarbie/area_params/get", area_id=1))["result"]
    assert again["params"]["first_clean_params"]["blade_height"] == 60
    bogus = await ws("yarbie/area_params/save", area_id=1, changes={"bogus": 1})
    assert bogus["error"]["code"] == "invalid"


async def test_route_preview_is_one_job(ws: WS, entry: MockConfigEntry) -> None:
    sim = entry.runtime_data.robot.simulator
    calls: list[object] = []
    original = sim.cmd_preview_plan_path

    def counted(value: object) -> None:
        calls.append(value)
        original(value)

    sim.cmd_preview_plan_path = counted  # type: ignore[method-assign]
    first = (await ws("yarbie/route/start", plan_id=1))["result"]
    second = (await ws("yarbie/route/start", plan_id=1))["result"]
    assert first["id"] == second["id"]  # a second click joins the first job

    client = ws.client
    await client.send_json_auto_id({"type": "yarbie/job/subscribe", "job_id": first["id"]})
    assert (await client.receive_json())["success"]
    job = (await client.receive_json())["event"]
    while job["status"] == "running":
        job = (await client.receive_json())["event"]
    assert job["status"] == "done", job
    result = job["result"]
    # The demo's route covers each area of the plan: rows across it and a lap of its edge.
    kinds = {(f["properties"]["area_id"], f["properties"]["kind"]) for f in result["features"]}
    assert kinds == {(1, "fill"), (1, "edge"), (2, "fill"), (2, "edge")}
    fill = next(f for f in result["features"] if f["properties"]["kind"] == "fill")
    assert len(fill["geometry"]["coordinates"]) > 20
    assert result["total_s"] > 600
    assert len(calls) == 1
    # Done jobs are kept: a reloaded panel gets the same result without asking the robot.
    again = (await ws("yarbie/route/start", plan_id=1))["result"]
    assert again["id"] == first["id"] and again["status"] == "done" and len(calls) == 1


async def test_progress_without_a_run(ws: WS) -> None:
    assert (await ws("yarbie/progress"))["result"] == {"running": False, "plan": None}


@pytest.mark.parametrize("options", [{OPT_ALLOW_WRITES: False}])
async def test_kill_switch_refuses_writes(ws: WS) -> None:
    body = await _map(ws)
    reply = await ws("yarbie/apply", features=body["features"], revision=body["revision"])
    assert reply["error"]["code"] == "read_only"
    assert (await ws("yarbie/info"))["result"]["read_only_reason"]


async def test_non_admins_are_refused(
    hass: HomeAssistant, hass_ws_client: Any, hass_read_only_access_token: str, entry: MockConfigEntry
) -> None:
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id({"type": "yarbie/map"})
    reply = await client.receive_json()
    assert reply["error"]["code"] == "unauthorized"


def test_finished_parts_from_records_and_bare_ids() -> None:
    from custom_components.yarbie.editor.runs import finished_parts

    records = {"finishIds": [{"id": 4, "move_type": 1, "clean_times": 1}, {"id": 5, "move_type": 0}]}
    assert finished_parts(records) == {4: {"fill": True, "edge": False}, 5: {"fill": False, "edge": True}}
    assert finished_parts({"finishIds": [7]}) == {7: {"fill": True, "edge": True}}
