async def test_verification_success(api_client):
    resp = await api_client.get(
        "/webhook/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "test-verify-token",
            "hub.challenge": "challenge-123",
        },
    )
    assert resp.status_code == 200
    assert resp.text == "challenge-123"


async def test_verification_wrong_token(api_client):
    resp = await api_client.get(
        "/webhook/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "nope",
            "hub.challenge": "challenge-123",
        },
    )
    assert resp.status_code == 403


async def test_post_invalid_signature_rejected(api_client):
    resp = await api_client.post(
        "/webhook/whatsapp",
        content=b'{"object":"whatsapp_business_account","entry":[]}',
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=deadbeef"},
    )
    assert resp.status_code == 403
    assert resp.json()["status"] == "invalid_signature"
