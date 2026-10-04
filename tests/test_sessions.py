from datetime import UTC, datetime, timedelta

NOW = datetime.now(UTC)


def ago(hours: float) -> str:
    return (NOW - timedelta(hours=hours)).isoformat()


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def switch(client, kind: str, hours_ago: float, **extra):
    resp = await client.post("/sessions/switch", json={"kind": kind, "at": ago(hours_ago), **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def timeline(client) -> list[dict]:
    return list(reversed((await client.get("/sessions")).json()))


async def test_switch_closes_current_at_same_instant(client):
    assert (await client.get("/sessions/current")).json() is None

    fast = await switch(client, "fast", 20)
    assert fast["target_hours"] == 16  # from the default 16:8 schedule
    eat = await switch(client, "eat", 4)
    assert eat["target_hours"] == 8
    assert eat["ended_at"] is None

    fast_after, eat_after = await timeline(client)
    assert fast_after["id"] == fast["id"]
    assert parse(fast_after["ended_at"]) == parse(eat_after["started_at"])
    assert (await client.get("/sessions/current")).json()["id"] == eat["id"]


async def test_switch_defaults_to_now_and_accepts_a_target(client):
    resp = await client.post("/sessions/switch", json={"kind": "fast", "target_hours": 20})
    assert resp.status_code == 201
    assert resp.json()["target_hours"] == 20
    assert abs(parse(resp.json()["started_at"]) - datetime.now(UTC)) < timedelta(minutes=1)


async def test_switch_validation(client):
    await switch(client, "fast", 10)
    same_kind = await client.post("/sessions/switch", json={"kind": "fast"})
    assert same_kind.status_code == 409

    before_current = await client.post("/sessions/switch", json={"kind": "eat", "at": ago(11)})
    assert before_current.status_code == 422

    future = await client.post("/sessions/switch", json={"kind": "eat", "at": ago(-2)})
    assert future.status_code == 422

    naive = await client.post("/sessions/switch", json={"kind": "eat", "at": "2026-01-01T10:00"})
    assert naive.status_code == 422

    bad_kind = await client.post("/sessions/switch", json={"kind": "sleep"})
    assert bad_kind.status_code == 422


async def test_switch_after_a_gap_cannot_overlap_previous(client):
    fast = await switch(client, "fast", 30)
    await client.patch(f"/sessions/{fast['id']}", json={"ended_at": ago(14)})
    assert (await client.get("/sessions/current")).json() is None

    overlap = await client.post("/sessions/switch", json={"kind": "eat", "at": ago(20)})
    assert overlap.status_code == 422
    await switch(client, "eat", 10)


async def test_moving_a_shared_boundary_moves_the_neighbour(client):
    fast = await switch(client, "fast", 20)
    eat = await switch(client, "eat", 4)

    resp = await client.patch(f"/sessions/{eat['id']}", json={"started_at": ago(6)})
    assert resp.status_code == 200
    fast_after, eat_after = await timeline(client)
    assert parse(fast_after["ended_at"]) == parse(ago(6)) == parse(eat_after["started_at"])

    emptied = await client.patch(f"/sessions/{eat['id']}", json={"started_at": ago(21)})
    assert emptied.status_code == 422

    resp = await client.patch(f"/sessions/{fast['id']}", json={"ended_at": ago(5)})
    assert resp.status_code == 200
    _, eat_after = await timeline(client)
    assert parse(eat_after["started_at"]) == parse(ago(5))


async def test_patch_rejects_overlap_and_reopening_past_sessions(client):
    first = await switch(client, "fast", 40)
    await client.patch(f"/sessions/{first['id']}", json={"ended_at": ago(30)})
    second = await switch(client, "eat", 20)  # leaves a 10h gap

    overlap = await client.patch(f"/sessions/{second['id']}", json={"started_at": ago(35)})
    assert overlap.status_code == 422

    reopen = await client.patch(f"/sessions/{first['id']}", json={"ended_at": None})
    assert reopen.status_code == 422

    notes = await client.patch(f"/sessions/{first['id']}", json={"notes": "felt good"})
    assert notes.status_code == 200
    assert notes.json()["notes"] == "felt good"


async def test_deleting_the_open_session_resumes_the_previous(client):
    eat = await switch(client, "eat", 10)
    fast = await switch(client, "fast", 1)

    assert (await client.delete(f"/sessions/{fast['id']}")).status_code == 204
    current = (await client.get("/sessions/current")).json()
    assert current["id"] == eat["id"]
    assert current["ended_at"] is None


async def test_deleting_a_middle_session_merges_its_neighbours(client):
    first = await switch(client, "eat", 30)
    middle = await switch(client, "fast", 20)
    last = await switch(client, "eat", 4)
    await switch(client, "fast", 1)

    assert (await client.delete(f"/sessions/{middle['id']}")).status_code == 204
    sessions = await timeline(client)
    assert [s["kind"] for s in sessions] == ["eat", "fast"]
    assert sessions[0]["id"] == first["id"]
    assert parse(sessions[0]["ended_at"]) == parse(sessions[1]["started_at"])
    assert all(s["id"] != last["id"] for s in sessions)


async def test_deleting_a_session_after_a_gap_leaves_the_gap(client):
    first = await switch(client, "fast", 30)
    await client.patch(f"/sessions/{first['id']}", json={"ended_at": ago(20)})
    later = await switch(client, "eat", 10)

    assert (await client.delete(f"/sessions/{later['id']}")).status_code == 204
    (only,) = await timeline(client)
    assert parse(only["ended_at"]) == parse(ago(20))


async def test_list_filters_by_range_and_kind(client):
    old = await switch(client, "fast", 24 * 40)
    await client.patch(f"/sessions/{old['id']}", json={"ended_at": ago(24 * 40 - 16)})
    fast = await switch(client, "fast", 10)
    eat = await switch(client, "eat", 2)

    assert [s["id"] for s in (await client.get("/sessions")).json()] == [eat["id"], fast["id"]]
    fasts = (await client.get("/sessions", params={"kind": "fast"})).json()
    assert [s["id"] for s in fasts] == [fast["id"]]

    today = NOW.date()
    wide = await client.get(
        "/sessions", params={"from": str(today - timedelta(days=45)), "to": str(today)}
    )
    assert [s["id"] for s in wide.json()] == [eat["id"], fast["id"], old["id"]]

    backwards = await client.get("/sessions", params={"from": str(today), "to": "2020-01-01"})
    assert backwards.status_code == 422


async def test_other_users_session_is_hidden(client, anon_client, google_claims):
    fast = await switch(client, "fast", 2)

    google_claims["sub"] = "google-other"
    other = (await anon_client.post("/auth/google", json={"id_token": "good-token"})).json()
    headers = {"Authorization": f"Bearer {other['access_token']}"}
    patch = await anon_client.patch(f"/sessions/{fast['id']}", json={}, headers=headers)
    assert patch.status_code == 404
    delete = await anon_client.delete(f"/sessions/{fast['id']}", headers=headers)
    assert delete.status_code == 404
