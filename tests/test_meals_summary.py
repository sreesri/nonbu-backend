from datetime import UTC, datetime, timedelta

OATS = {
    "name": "Oats",
    "quantity": 1,
    "unit": "bowl",
    "calories": 300,
    "protein_g": 10,
    "carbs_g": 50,
    "fat_g": 6,
    "fiber_g": 8,
}


async def add(client, items=None, **overrides):
    body = {"meal_type": "breakfast", "items": items or [OATS], **overrides}
    resp = await client.post("/meals", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_meal_crud(client):
    meal = await add(client, items=[OATS, {"name": "Banana", "calories": 105, "servings": 2}])
    assert meal["eaten_at"] is not None
    assert [i["name"] for i in meal["items"]] == ["Oats", "Banana"]
    assert meal["items"][0]["servings"] == 1
    # The meal's nutrition is the sum of its dishes, times their servings.
    assert meal["totals"] == {
        "calories": 510,
        "protein_g": 10,
        "carbs_g": 50,
        "fat_g": 6,
        "fiber_g": 8,
    }

    listed = (await client.get("/meals")).json()
    assert [m["id"] for m in listed] == [meal["id"]]

    resp = await client.patch(f"/meals/{meal['id']}", json={"name": "Big breakfast", "items": None})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Big breakfast"
    assert len(resp.json()["items"]) == 2  # required field not cleared

    replaced = await client.patch(f"/meals/{meal['id']}", json={"items": [{"name": "Tea"}]})
    assert [i["name"] for i in replaced.json()["items"]] == ["Tea"]
    assert replaced.json()["totals"]["calories"] == 0  # unknown nutrition counts as 0

    assert (await client.delete(f"/meals/{meal['id']}")).status_code == 204
    assert (await client.get(f"/meals/{meal['id']}")).status_code == 404


async def test_meal_validation(client):
    bad = [
        {"meal_type": "brunch", "items": [OATS]},
        {"meal_type": "snack", "items": []},
        {"meal_type": "snack", "items": [{**OATS, "calories": -5}]},
        {"meal_type": "snack", "items": [{**OATS, "servings": 0}]},
        {"meal_type": "snack", "items": [{**OATS, "name": ""}]},
    ]
    for body in bad:
        assert (await client.post("/meals", json=body)).status_code == 422, body
    meal = await add(client)
    resp = await client.patch(f"/meals/{meal['id']}", json={"items": []})
    assert resp.status_code == 422


async def test_meals_are_private(client, anon_client, google_claims):
    meal = await add(client)
    google_claims.update(sub="google-other", email="me@example.com")
    other = (await anon_client.post("/auth/google", json={"id_token": "good-token"})).json()
    headers = {"Authorization": f"Bearer {other['access_token']}"}
    assert (await anon_client.get(f"/meals/{meal['id']}", headers=headers)).status_code == 404
    assert (await anon_client.get("/meals", headers=headers)).json() == []


async def test_meal_day_uses_user_timezone(client):
    await client.patch("/me", json={"timezone": "Asia/Kolkata"})
    # 2026-03-10 20:00 UTC is 2026-03-11 01:30 in India.
    await add(client, eaten_at="2026-03-10T20:00:00Z", meal_type="snack")

    assert (await client.get("/meals", params={"date": "2026-03-10"})).json() == []
    ist_day = (await client.get("/meals", params={"date": "2026-03-11"})).json()
    assert len(ist_day) == 1


async def test_daily_summary(client):
    await client.patch("/me", json={"goals": {"daily_calories": 2000, "fiber_g": 30}})
    await add(client, eaten_at="2026-03-10T08:00:00Z")
    rice = {"name": "Rice", "calories": 250, "protein_g": 4, "carbs_g": 55, "fat_g": 0.5}
    dal = {"name": "Dal", "fiber_g": 2.5}
    await add(
        client,
        items=[{**rice, "servings": 2}, dal],
        eaten_at="2026-03-10T13:00:00Z",
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
