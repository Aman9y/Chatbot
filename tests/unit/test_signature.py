from app.security.signature import sign_body, verify_signature

SECRET = "s3cr3t"
BODY = b'{"object":"whatsapp_business_account","entry":[]}'


def test_round_trip():
    header = sign_body(SECRET, BODY)
    assert header.startswith("sha256=")
    assert verify_signature(SECRET, BODY, header) is True


def test_rejects_tampered_body():
    header = sign_body(SECRET, BODY)
    assert verify_signature(SECRET, BODY + b" ", header) is False


def test_rejects_wrong_secret():
    header = sign_body("other", BODY)
    assert verify_signature(SECRET, BODY, header) is False


def test_rejects_missing_or_malformed_header():
    assert verify_signature(SECRET, BODY, None) is False
    assert verify_signature(SECRET, BODY, "") is False
    assert verify_signature(SECRET, BODY, "md5=abc") is False
    assert verify_signature("", BODY, sign_body(SECRET, BODY)) is False
