from datetime import UTC, date, datetime, timedelta

from app.routers.summary import count_streak, goal_days

GOAL = 16


def midnight_utc(days_ago: int) -> datetime:
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=days_ago)


async def log_fast(client, days_ago: int, hours: float = GOAL) -> None:
    """A fast of `hours` (goal 16h) that ends on the UTC day `days_ago`, then an eating window."""
    end = midnight_utc(days_ago) + timedelta(hours=17)
    start = end - timedelta(hours=hours)
    for kind, at in (("fast", start), ("eat", end)):
        body = {
            "kind": kind,
            "at": at.isoformat(),
            "target_hours": GOAL if kind == "fast" else None,
        }
        resp = await client.post("/sessions/switch", json=body)
        assert resp.status_code == 201, resp.text


async def get_streak(client) -> int:
    resp = await client.get("/summary/streak")
    assert resp.status_code == 200, resp.text
    return resp.json()["days"]


async def test_streak_counts_days_a_goal_fast_ended(client):
    assert await get_streak(client) == 0
    for days_ago in (3, 2, 1):
        await log_fast(client, days_ago)
    # Today has no finished fast yet, which doesn't break the streak.
    assert await get_streak(client) == 3


async def test_streak_breaks_on_a_missed_day(client):
    for days_ago in (4, 2, 1):
        await log_fast(client, days_ago)
    assert await get_streak(client) == 2


async def test_fast_short_of_its_goal_does_not_count(client):
    await log_fast(client, 2)
    await log_fast(client, 1, hours=GOAL - 0.5)
    assert await get_streak(client) == 0


async def test_open_fast_does_not_count_yet(client):
    await log_fast(client, 1)
    start = datetime.now(UTC) - timedelta(hours=GOAL + 1)
    await client.post("/sessions/switch", json={"kind": "fast", "at": start.isoformat()})
    assert await get_streak(client) == 1


def test_goal_days_use_the_local_end_date():
    start = datetime(2026, 3, 10, 4, tzinfo=UTC)
    end = start + timedelta(hours=GOAL)  # 20:00 UTC = 01:30 next day in India
    assert goal_days([(start, end, GOAL)], "Asia/Kolkata") == {date(2026, 3, 11)}
    assert goal_days([(start, end, GOAL)], "UTC") == {date(2026, 3, 10)}


def test_goal_days_across_a_dst_change():
    # New York springs forward on 2026-03-08; this 16h fast spans the missing hour.
    start = datetime(2026, 3, 8, 5, tzinfo=UTC)  # 00:00 EST
    end = start + timedelta(hours=GOAL)  # 17:00 EDT the same day
    assert goal_days([(start, end, GOAL)], "America/New_York") == {date(2026, 3, 8)}


def test_count_streak():
    today = date(2026, 10, 10)
    days = {today - timedelta(days=n) for n in (0, 1, 2, 4)}
    assert count_streak(days, today) == 3
    assert count_streak(days - {today}, today) == 2
    assert count_streak({today - timedelta(days=2)}, today) == 0
    assert count_streak(set(), today) == 0
