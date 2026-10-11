async def test_health(anon_client):
    resp = await anon_client.get("/health")
    assert resp.json() == {"status": "ok"}


async def test_login_creates_user(client):
    resp = await client.get("/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "me@example.com"
    assert body["name"] == "Me"
    assert body["timezone"] == "UTC"
    assert body["goals"]["default_fast_hours"] == 16


async def test_login_is_idempotent(anon_client, google_claims):
    for _ in range(2):
        resp = await anon_client.post("/auth/google", json={"id_token": "good-token"})
        assert resp.status_code == 200
    token = resp.json()["access_token"]
    me = await anon_client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["id"] == 1


async def test_invalid_google_token(anon_client, google_claims):
    resp = await anon_client.post("/auth/google", json={"id_token": "forged"})
    assert resp.status_code == 401


async def test_email_not_allowlisted(anon_client, google_claims):
    google_claims["email"] = "stranger@example.com"
    resp = await anon_client.post("/auth/google", json={"id_token": "good-token"})
    assert resp.status_code == 403


async def test_unverified_email_rejected(anon_client, google_claims):
    google_claims["email_verified"] = False
    resp = await anon_client.post("/auth/google", json={"id_token": "good-token"})
    assert resp.status_code == 403


async def test_requires_bearer(anon_client):
    assert (await anon_client.get("/me")).status_code == 401
    resp = await anon_client.get("/me", headers={"Authorization": "Bearer junk"})
    assert resp.status_code == 401


async def test_refresh_rotates_token(anon_client, tokens):
    resp = await anon_client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 200
    new = resp.json()
    assert new["refresh_token"] != tokens["refresh_token"]

    me = await anon_client.get("/me", headers={"Authorization": f"Bearer {new['access_token']}"})
    assert me.status_code == 200

    # Once the new refresh token has been used, the old one is dead.
    newer = await anon_client.post("/auth/refresh", json={"refresh_token": new["refresh_token"]})
    assert newer.status_code == 200
    reused = await anon_client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert reused.status_code == 401


async def refresh(client, token):
    return await client.post("/auth/refresh", json={"refresh_token": token})


async def test_refresh_survives_a_lost_response(anon_client, tokens):
    # The server rotates the token, but the app never receives the reply.
    lost = (await refresh(anon_client, tokens["refresh_token"])).json()

    # Retrying with the old token works, and the reply it never got is retired instead.
    retried = await refresh(anon_client, tokens["refresh_token"])
    assert retried.status_code == 200
    assert (await refresh(anon_client, lost["refresh_token"])).status_code == 401

    # It can happen more than once.
    again = await refresh(anon_client, tokens["refresh_token"])
    assert again.status_code == 200
    assert (await refresh(anon_client, retried.json()["refresh_token"])).status_code == 401
    assert (await refresh(anon_client, again.json()["refresh_token"])).status_code == 200
    assert (await refresh(anon_client, tokens["refresh_token"])).status_code == 401


async def test_logout_revokes_refresh(anon_client, tokens):
    resp = await anon_client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 204
    resp = await anon_client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401


async def test_patch_me(client):
    resp = await client.patch(
        "/me",
        json={
            "timezone": "Asia/Kolkata",
            "goals": {"daily_calories": 1800, "protein_g": 120, "default_fast_hours": 18},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["timezone"] == "Asia/Kolkata"
    assert body["goals"]["daily_calories"] == 1800
    assert body["goals"]["default_fast_hours"] == 18
    assert body["goals"]["eating_window_hours"] == 6

    bad = await client.patch("/me", json={"timezone": "Mars/Base"})
    assert bad.status_code == 422


async def test_eating_window_follows_fasting_hours(client):
    me = (await client.get("/me")).json()
    assert me["goals"]["eating_window_hours"] == 8  # default 16:8

    resp = await client.patch("/me", json={"goals": {"default_fast_hours": 20}})
    assert resp.status_code == 200
    assert resp.json()["goals"]["eating_window_hours"] == 4

    # Persisted, not just echoed back.
    assert (await client.get("/me")).json()["goals"]["eating_window_hours"] == 4


async def test_fasting_hours_must_leave_an_eating_window(client):
    for hours in (0, 24, 36):
        resp = await client.patch("/me", json={"goals": {"default_fast_hours": hours}})
        assert resp.status_code == 422, hours
    assert (await client.get("/me")).json()["goals"]["default_fast_hours"] == 16
