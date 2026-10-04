from datetime import UTC, datetime, timedelta


async def add(client, **overrides):
    body = {
        "name": "Oats",
        "meal_type": "breakfast",
        "calories": 300,
        "protein_g": 10,
        "carbs_g": 50,
        "fat_g": 6,
        "fiber_g": 8,
        **overrides,
    }
    resp = await client.post("/food", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_food_crud(client):
    entry = await add(client)
    assert entry["eaten_at"] is not None

    listed = (await client.get("/food")).json()
    assert [e["id"] for e in listed] == [entry["id"]]

    resp = await client.patch(f"/food/{entry['id']}", json={"calories": 350, "name": None})
    assert resp.status_code == 200
    assert resp.json()["calories"] == 350
    assert resp.json()["name"] == "Oats"  # required field not cleared
    assert resp.json()["fiber_g"] == 8

    cleared = await client.patch(f"/food/{entry['id']}", json={"fiber_g": None})
    assert cleared.json()["fiber_g"] is None

    assert (await client.delete(f"/food/{entry['id']}")).status_code == 204
    assert (await client.get(f"/food/{entry['id']}")).status_code == 404


async def test_food_validation(client):
    resp = await client.post("/food", json={"name": "X", "meal_type": "brunch"})
    assert resp.status_code == 422
    resp = await client.post("/food", json={"name": "X", "meal_type": "snack", "calories": -5})
    assert resp.status_code == 422
    resp = await client.post("/food", json={"name": "X", "meal_type": "snack", "fiber_g": -1})
    assert resp.status_code == 422


async def test_food_day_uses_user_timezone(client):
    await client.patch("/me", json={"timezone": "Asia/Kolkata"})
    # 2026-03-10 20:00 UTC is 2026-03-11 01:30 in India.
    await add(client, eaten_at="2026-03-10T20:00:00Z", meal_type="snack")

    assert (await client.get("/food", params={"date": "2026-03-10"})).json() == []
    ist_day = (await client.get("/food", params={"date": "2026-03-11"})).json()
    assert len(ist_day) == 1


async def test_recent_dedupes_by_name(client):
    await add(client, name="Oats", eaten_at="2026-03-01T08:00:00Z")
    await add(client, name="oats ", eaten_at="2026-03-02T08:00:00Z", calories=320)
    await add(client, name="Eggs", eaten_at="2026-03-02T09:00:00Z")

    recent = (await client.get("/food/recent")).json()
    assert [r["name"] for r in recent] == ["Eggs", "oats "]
    assert recent[1]["calories"] == 320


async def test_daily_summary(client):
    await client.patch("/me", json={"goals": {"daily_calories": 2000, "fiber_g": 30}})
    await add(client, eaten_at="2026-03-10T08:00:00Z")
    await add(
        client,
        eaten_at="2026-03-10T13:00:00Z",
        name="Rice",
        calories=500,
        protein_g=8,
        carbs_g=110,
        fat_g=1,
        fiber_g=2.5,
        meal_type="lunch",
    )
    await add(client, eaten_at="2026-03-11T08:00:00Z")  # next day

    # A fast from 2026-03-09 20:00 to 2026-03-10 12:00 UTC -> 12h on the 10th.
    now = datetime.now(UTC)
    assert now > datetime(2026, 3, 10, 12, tzinfo=UTC)
    await client.post("/sessions/switch", json={"kind": "fast", "at": "2026-03-09T20:00:00Z"})
    # The eating session that follows must not count as fasting.
    await client.post("/sessions/switch", json={"kind": "eat", "at": "2026-03-10T12:00:00Z"})

    summary = (await client.get("/summary/daily", params={"date": "2026-03-10"})).json()
    assert summary["entry_count"] == 2
    assert summary["totals"] == {
        "calories": 800,
        "protein_g": 18,
        "carbs_g": 160,
        "fat_g": 7,
        "fiber_g": 10.5,
    }
    assert summary["goals"]["daily_calories"] == 2000
    assert summary["goals"]["fiber_g"] == 30
    assert summary["fasting_hours"] == 12


async def test_range_summary(client):
    await add(client, eaten_at="2026-03-10T08:00:00Z")
    resp = await client.get("/summary/range", params={"from": "2026-03-09", "to": "2026-03-11"})
    days = resp.json()
    assert [d["date"] for d in days] == ["2026-03-09", "2026-03-10", "2026-03-11"]
    assert [d["totals"]["calories"] for d in days] == [0, 300, 0]

    too_long = await client.get("/summary/range", params={"from": "2026-01-01", "to": "2026-12-31"})
    assert too_long.status_code == 422


async def test_open_fast_counts_until_now(client):
    now = datetime.now(UTC)
    await client.post(
        "/sessions/switch", json={"kind": "fast", "at": (now - timedelta(hours=3)).isoformat()}
    )
    today = (await client.get("/summary/daily")).json()
    # Up to 3h, possibly split across a UTC day boundary.
    assert 0 < today["fasting_hours"] <= 3.01
