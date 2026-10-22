"""End to end through the HTTP API against yarbo-local's simulator."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from yarbie import demo
from yarbie.config import Settings
from yarbie.main import create_app


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        source="sim", host=None, serial=None, email=None, password=None, data_dir=tmp_path
    )
    with TestClient(create_app(settings)) as c:
        yield c


def _square(lon: float, lat: float, d: float = 0.00002) -> list[list[float]]:
    return [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]


def test_status_and_map(client: TestClient) -> None:
    status = client.get("/api/status").json()
    assert status["source"] == "sim" and status["writable"] and not status["busy"]
    body = client.get("/api/map").json()
    assert body["reference"] == list(demo.REFERENCE)
    families = {f["properties"]["family"] for f in body["features"]["features"]}
    assert {"areas", "nogozones", "pathways", "dock"} <= families
    assert client.get("/api/status").json()["position"] is not None


def test_create_edit_delete_nogozone(client: TestClient, tmp_path: Path) -> None:
    body = client.get("/api/map").json()
    fc = body["features"]
    lat, lon = demo.REFERENCE
    fc["features"].append({
        "type": "Feature", "id": "new:1",
        "geometry": {"type": "Polygon", "coordinates": [_square(lon - 0.0001, lat + 0.0001)]},
        "properties": {"family": "nogozones", "name": "Pond"},
    })
    ops = client.post("/api/map/preview", json=fc).json()["ops"]
    assert [(o["kind"], o["family"], o["supported"]) for o in ops] == [("create", "nogozones", True)]

    result = client.post("/api/map/apply", json={"features": fc, "revision": body["revision"]})
    assert result.status_code == 200, result.text
    result = result.json()
    assert result["error"] is None and len(result["applied"]) == 1
    assert (tmp_path / "backups" / result["backup"]).exists()
    pond = next(f for f in result["map"]["features"]["features"] if f["properties"]["name"] == "Pond")
    assert pond["properties"]["id"] >= 100

    # Rename it, then delete it.
    fc = result["map"]["features"]
    next(f for f in fc["features"] if f["id"] == pond["id"])["properties"]["name"] = "Koi pond"
    renamed = client.post(
        "/api/map/apply", json={"features": fc, "revision": result["map"]["revision"]}
    ).json()
    assert [o["changes"] for o in renamed["applied"]] == [["name"]]
    fc = renamed["map"]["features"]
    fc["features"] = [f for f in fc["features"] if f["id"] != pond["id"]]
    deleted = client.post(
        "/api/map/apply", json={"features": fc, "revision": renamed["map"]["revision"]}
    ).json()
    assert [o["kind"] for o in deleted["applied"]] == ["delete"]
    names = {f["properties"]["name"] for f in deleted["map"]["features"]["features"]}
    assert "Koi pond" not in names and "Flower bed" in names


def test_stale_revision_is_refused(client: TestClient) -> None:
    fc = client.get("/api/map").json()["features"]
    response = client.post("/api/map/apply", json={"features": fc, "revision": "stale"})
    assert response.status_code == 409


def test_unsupported_ops_are_skipped_not_sent(client: TestClient) -> None:
    body = client.get("/api/map").json()
    fc = body["features"]
    fc["features"] = [f for f in fc["features"] if f["id"] != "areas:2"]
    result = client.post("/api/map/apply", json={"features": fc, "revision": body["revision"]}).json()
    assert result["applied"] == []
    assert [o["kind"] for o in result["skipped"]] == ["delete"]
    after = client.get("/api/map").json()
    assert any(f["id"] == "areas:2" for f in after["features"]["features"])


def test_area_rename(client: TestClient) -> None:
    body = client.get("/api/map").json()
    fc = body["features"]
    next(f for f in fc["features"] if f["id"] == "areas:1")["properties"]["name"] = "Front"
    result = client.post("/api/map/apply", json={"features": fc, "revision": body["revision"]}).json()
    assert result["error"] is None
    assert any(f["properties"]["name"] == "Front" for f in result["map"]["features"]["features"])


def test_plans_route_and_params(client: TestClient) -> None:
    plans = client.get("/api/plans").json()
    assert [p["name"] for p in plans] == ["Whole yard", "Front only"]
    created = client.post("/api/plans", json={"name": "Back only", "area_ids": [2]}).json()
    assert any(p["id"] == created["id"] for p in client.get("/api/plans").json())
    route = client.get("/api/plans/1/route").json()
    assert route["features"] and route["total_s"] == 600.0
    params = client.get("/api/areas/1/mower-params").json()
    assert params["params"]["first_clean_params"]["blade_height"] == 50
    patched = client.patch(
        "/api/areas/1/mower-params", json={"first_clean_params": {"blade_height": 60}}
    )
    assert patched.status_code == 200, patched.text
    again = client.get("/api/areas/1/mower-params").json()
    assert again["params"]["first_clean_params"]["blade_height"] == 60
    assert client.patch("/api/areas/1/mower-params", json={"bogus": 1}).status_code == 400


def test_progress_without_a_run(client: TestClient) -> None:
    assert client.get("/api/progress").json() == {"running": False, "plan": None}


def test_finished_parts_from_records_and_bare_ids() -> None:
    from yarbie.api import _finished

    records = {"finishIds": [{"id": 4, "move_type": 1, "clean_times": 1}, {"id": 5, "move_type": 0}]}
    assert _finished(records) == {(4, "fill"), (5, "edge")}
    assert _finished({"finishIds": [7]}) == {(7, "fill"), (7, "edge")}
