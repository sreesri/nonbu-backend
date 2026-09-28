import os
import tempfile

_db_dir = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_db_dir}/test.db"
os.environ["JWT_SECRET"] = "test-secret-at-least-32-bytes-long!!"
os.environ["GOOGLE_CLIENT_IDS"] = "web-client-id"
os.environ["ALLOWED_EMAILS"] = "me@example.com"

import httpx  # noqa: E402
import pytest  # noqa: E402

from app import auth  # noqa: E402
from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture(autouse=True)
async def reset_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest.fixture
def google_claims(monkeypatch):
    """Claims returned by the (mocked) Google verifier; mutate to simulate other accounts."""
    claims = {
        "sub": "google-123",
        "email": "me@example.com",
        "email_verified": True,
        "name": "Me",
        "picture": "https://example.com/me.png",
    }

    def fake_verify(token, settings):
        if token != "good-token":
            raise ValueError("bad token")
        return claims

    monkeypatch.setattr(auth, "verify_google_id_token", fake_verify)
    return claims


@pytest.fixture
async def anon_client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def tokens(anon_client, google_claims):
    resp = await anon_client.post("/auth/google", json={"id_token": "good-token"})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
async def client(anon_client, tokens):
    anon_client.headers["Authorization"] = f"Bearer {tokens['access_token']}"
    return anon_client
