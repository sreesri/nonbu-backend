from datetime import datetime, timedelta, timezone


def iso(dt: datetime) -> str:
    return dt.isoformat()


async def test_start_and_end_fast(client):
    assert (await client.get("/fasts/current")).json() is None

    resp = await client.post("/fasts/start", json={})
    assert resp.status_code == 201
    fast = resp.json()
    assert fast["target_hours"] == 16  # user's default
    assert fast["ended_at"] is None

    current = (await client.get("/fasts/current")).json()
    assert current["id"] == fast["id"]

    resp = await client.post(f"/fasts/{fast['id']}/end", json={})
    assert resp.status_code == 200
    assert resp.json()["ended_at"] is not None
    assert (await client.get("/fasts/current")).json() is None


async def test_only_one_open_fast(client):
    assert (await client.post("/fasts/start", json={})).status_code == 201
    assert (await client.post("/fasts/start", json={})).status_code == 409


async def test_cannot_end_twice(client):
    fast = (await client.post("/fasts/start", json={})).json()
    await client.post(f"/fasts/{fast['id']}/end", json={})
    resp = await client.post(f"/fasts/{fast['id']}/end", json={})
    assert resp.status_code == 409


async def test_backdated_start_and_validation(client):
    now = datetime.now(timezone.utc)
    resp = await client.post(
        "/fasts/start",
        json={"started_at": iso(now - timedelta(hours=10)), "target_hours": 14},
    )
    assert resp.status_code == 201
    fast = resp.json()
    assert fast["target_hours"] == 14

    before_start = await client.post(
        f"/fasts/{fast['id']}/end", json={"ended_at": iso(now - timedelta(hours=11))}
    )
    assert before_start.status_code == 422

    future = await client.post(
        "/fasts/start", json={"started_at": iso(now + timedelta(hours=2))}
    )
    assert future.status_code in (409, 422)

    naive = await client.post("/fasts/start", json={"started_at": "2026-01-01T10:00:00"})
    assert naive.status_code == 422


async def test_patch_reopen_conflicts_with_open_fast(client):
    now = datetime.now(timezone.utc)
    old = (
        await client.post(
            "/fasts/start", json={"started_at": iso(now - timedelta(hours=30))}
        )
    ).json()
    await client.post(
        f"/fasts/{old['id']}/end", json={"ended_at": iso(now - timedelta(hours=14))}
    )
    await client.post("/fasts/start", json={})

    resp = await client.patch(f"/fasts/{old['id']}", json={"ended_at": None})
    assert resp.status_code == 409

    resp = await client.patch(f"/fasts/{old['id']}", json={"notes": "felt good"})
    assert resp.status_code == 200
    assert resp.json()["notes"] == "felt good"


async def test_list_and_delete(client):
    now = datetime.now(timezone.utc)
    old = (
        await client.post(
            "/fasts/start", json={"started_at": iso(now - timedelta(days=40))}
        )
    ).json()
    await client.post(
        f"/fasts/{old['id']}/end",
        json={"ended_at": iso(now - timedelta(days=40) + timedelta(hours=16))},
    )
    recent = (await client.post("/fasts/start", json={})).json()

    listed = (await client.get("/fasts")).json()
    assert [f["id"] for f in listed] == [recent["id"]]

    today = now.date()
    wide = (
        await client.get(
            "/fasts", params={"from": str(today - timedelta(days=45)), "to": str(today)}
        )
    ).json()
    assert [f["id"] for f in wide] == [recent["id"], old["id"]]

    assert (await client.delete(f"/fasts/{recent['id']}")).status_code == 204
    assert (await client.get("/fasts/current")).json() is None


async def test_other_users_fast_is_hidden(client, anon_client, google_claims):
    fast = (await client.post("/fasts/start", json={})).json()

    google_claims["sub"] = "google-other"
    other = (await anon_client.post("/auth/google", json={"id_token": "good-token"})).json()
    headers = {"Authorization": f"Bearer {other['access_token']}"}
    resp = await anon_client.post(f"/fasts/{fast['id']}/end", json={}, headers=headers)
    assert resp.status_code == 404
