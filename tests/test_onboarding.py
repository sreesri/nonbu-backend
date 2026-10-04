from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

KOLKATA = "Asia/Kolkata"


def hours_from_now(hours: float) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def onboarding(current: dict, fast_hours: float = 18) -> dict:
    return {
        "timezone": KOLKATA,
        "goals": {"default_fast_hours": fast_hours, "daily_calories": 1800},
        "current": current,
    }


async def test_new_user_is_not_onboarded(client):
    assert (await client.get("/me")).json()["onboarded_at"] is None


async def test_onboarding_while_fasting(client):
    started = hours_from_now(-5)
    resp = await client.post(
        "/me/onboarding", json=onboarding({"kind": "fast", "started_at": started})
    )
    assert resp.status_code == 200, resp.text
    me = resp.json()
    assert me["onboarded_at"] is not None
    assert me["timezone"] == KOLKATA
    assert me["goals"]["default_fast_hours"] == 18
    assert me["goals"]["daily_calories"] == 1800

    current = (await client.get("/sessions/current")).json()
    assert current["kind"] == "fast"
    assert current["target_hours"] == 18  # from the schedule just chosen
    assert parse(current["started_at"]) == parse(started)


async def test_onboarding_while_eating(client):
    resp = await client.post(
        "/me/onboarding", json=onboarding({"kind": "eat", "started_at": hours_from_now(-2)})
    )
    assert resp.status_code == 200
    current = (await client.get("/sessions/current")).json()
    assert current["kind"] == "eat"
    assert current["target_hours"] == 6  # 24 - 18


async def test_onboarding_with_a_planned_fast_counts_eating_from_local_midnight(client):
    fast_at = hours_from_now(3)
    resp = await client.post("/me/onboarding", json=onboarding({"kind": "eat", "fast_at": fast_at}))
    assert resp.status_code == 200

    current = (await client.get("/sessions/current")).json()
    started = parse(current["started_at"])
    local_start = started.astimezone(ZoneInfo(KOLKATA))
    # Today's midnight in the user's timezone (18:30 UTC the day before), not UTC midnight.
    assert (local_start.hour, local_start.minute) == (0, 0)
    assert local_start.date() == datetime.now(ZoneInfo(KOLKATA)).date()
    ends = started + timedelta(hours=current["target_hours"])
    assert abs(ends - parse(fast_at)) < timedelta(seconds=1)


async def test_onboarding_only_once(client):
    body = onboarding({"kind": "eat", "started_at": hours_from_now(-1)})
    assert (await client.post("/me/onboarding", json=body)).status_code == 200
    assert (await client.post("/me/onboarding", json=body)).status_code == 409


async def test_onboarding_validation(client):
    invalid = [
        {"kind": "eat"},  # no anchor
        {"kind": "eat", "started_at": hours_from_now(-1), "fast_at": hours_from_now(1)},
        {"kind": "fast", "fast_at": hours_from_now(2)},  # a plan is only for eating
        {"kind": "eat", "fast_at": hours_from_now(-1)},  # planned time already passed
        {"kind": "eat", "fast_at": hours_from_now(24 * 30)},  # beyond the session limit
        {"kind": "fast", "started_at": hours_from_now(2)},  # started in the future
    ]
    for current in invalid:
        resp = await client.post("/me/onboarding", json=onboarding(current))
        assert resp.status_code == 422, current

    bad_tz = onboarding({"kind": "fast", "started_at": hours_from_now(-1)})
    bad_tz["timezone"] = "Mars/Base"
    assert (await client.post("/me/onboarding", json=bad_tz)).status_code == 422

    # A rejected attempt saves nothing.
    me = (await client.get("/me")).json()
    assert me["onboarded_at"] is None
    assert me["goals"]["default_fast_hours"] == 16
    assert (await client.get("/sessions/current")).json() is None
