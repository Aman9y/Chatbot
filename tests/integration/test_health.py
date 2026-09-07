async def test_root(api_client):
    resp = await api_client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "mbbs-abroad-lead-bot"
    assert body["phase"] == 2


async def test_healthz(api_client):
    resp = await api_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readyz_ok(api_client):
    resp = await api_client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] == "ok"


async def test_request_id_header(api_client):
    resp = await api_client.get("/healthz")
    assert resp.headers.get("X-Request-ID")
