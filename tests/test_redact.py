from flakehound.report.redact import REDACTED, redact, truncate

# Built from pieces so this file never contains a real-looking secret.
FAKE_GH = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
FAKE_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"


def test_known_token_formats():
    out = redact(f"token {FAKE_GH} and {FAKE_AWS}", environ={})
    assert FAKE_GH not in out
    assert FAKE_AWS not in out
    assert out.count(REDACTED) == 2


def test_assignments_with_secret_names():
    out = redact("password=hunter22 api_key: 'abc123xyz' user=bob", environ={})
    assert "hunter22" not in out
    assert "abc123xyz" not in out
    assert "user=bob" in out


def test_bearer_and_url_credentials():
    out = redact("Authorization: Bearer abcdefghijkl https://me:pw123@example.com/x", environ={})
    assert "abcdefghijkl" not in out
    assert "pw123" not in out
    assert "example.com/x" in out


def test_values_of_secret_env_vars():
    env = {"MY_SERVICE_TOKEN": "s3cr3t-value", "HOME": "/home/me"}
    out = redact("got s3cr3t-value in /home/me", environ=env)
    assert "s3cr3t-value" not in out
    assert "/home/me" in out  # non-secret env values are untouched


def test_private_key_block():
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
    assert "MIIabc" not in redact(f"x {key} y", environ={})


def test_plain_text_untouched():
    text = "AssertionError: assert ['apple'] == []"
    assert redact(text, environ={}) == text


def test_truncate():
    assert truncate("abc", 10) == "abc"
    assert len(truncate("x" * 5000, 100)) <= 100


def test_stateless_github_app_token_is_fully_redacted():
    # New GitHub App installation tokens (and Actions' GITHUB_TOKEN): "ghs_" + a JWT,
    # ~520 chars with dots, dashes and underscores. No part of it may survive.
    header = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9"
    payload = "eyJpc3MiOiJnaXRodWIiLCJleHAiOjE3OTk5OTk5OTl9" + "Ab-_9z" * 60
    signature = "c2lnbmF0dXJl-_" * 12
    token = "ghs" + "_" + ".".join([header, payload, signature])
    assert len(token) > 500
    out = redact(f"Authorization failed for {token}.", environ={})
    assert out == f"Authorization failed for {REDACTED}"
    for part in (header, payload[:40], signature[:20]):
        assert part not in out
