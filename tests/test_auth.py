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
    resp = await anon_client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert resp.status_code == 200
    new = resp.json()
    assert new["refresh_token"] != tokens["refresh_token"]

    # The old refresh token is single-use.
    reused = await anon_client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert reused.status_code == 401

    me = await anon_client.get(
        "/me", headers={"Authorization": f"Bearer {new['access_token']}"}
    )
    assert me.status_code == 200


async def test_logout_revokes_refresh(anon_client, tokens):
    resp = await anon_client.post(
        "/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert resp.status_code == 204
    resp = await anon_client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
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

    bad = await client.patch("/me", json={"timezone": "Mars/Base"})
    assert bad.status_code == 422
