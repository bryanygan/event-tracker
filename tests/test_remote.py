"""Phone editing through a Cloudflare Tunnel: only with a valid Cloudflare Access login for this app."""
import json
import time

import jwt
import pytest
import responses
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from tracker import api, config, db, remote

HOST = "events.example.test"
TEAM = "https://team.cloudflareaccess.test"
AUD = "aud-tag-123"
CERTS = f"{TEAM}/cdn-cgi/access/certs"
NOTE = {"kind": "search", "text": "people searching Nova Vale tickets", "terms": ["Nova Vale"],
        "scope": "philly", "confidence": "high"}

TEAM_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks(*pairs):
    keys = []
    for kid, key in pairs:
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        keys.append({**jwk, "kid": kid, "alg": "RS256", "use": "sig"})
    return {"keys": keys}


def token(key=TEAM_KEY, kid="k1", alg="RS256", **claims):
    now = int(time.time())
    body = {"aud": [AUD], "iss": TEAM, "email": "me@example.test", "iat": now, "exp": now + 3600, **claims}
    return jwt.encode({k: v for k, v in body.items() if v is not None}, key, algorithm=alg, headers={"kid": kid})


@pytest.fixture
def certs():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.get(CERTS, json=_jwks(("k1", TEAM_KEY)))
        yield rsps


@pytest.fixture
def client(tmp_path, monkeypatch, certs):
    path = tmp_path / "api.db"
    monkeypatch.setattr(api, "conn", lambda: db.connect(path))
    db.connect(path).close()
    monkeypatch.setattr(config, "REMOTE_HOST", HOST)
    monkeypatch.setattr(config, "CF_ACCESS_TEAM_DOMAIN", TEAM)
    monkeypatch.setattr(config, "CF_ACCESS_AUD", AUD)
    monkeypatch.setattr(remote, "_keys", {})
    monkeypatch.setattr(remote, "_keys_at", 0.0)
    monkeypatch.setattr(remote, "_fetch_times", [])
    return TestClient(api.app)


def phone(tok=None, **extra):
    h = {"host": HOST, **extra}
    if tok is not None:
        h[remote.HEADER] = tok
    return h


def test_off_by_default_the_tunnel_host_is_just_an_unknown_host(client, monkeypatch):
    monkeypatch.setattr(config, "CF_ACCESS_AUD", "")
    assert client.get("/api/trends", headers=phone(token())).status_code == 403


def test_reads_need_a_valid_access_login(client):
    assert client.get("/api/trends", headers=phone()).status_code == 403
    assert client.get("/api/trends", headers=phone("not-a-jwt")).status_code == 403
    assert client.get("/api/trends", headers=phone(token())).status_code == 200
    assert client.get("/", headers=phone()).status_code == 403  # the page too, not just the API


@pytest.mark.parametrize("bad", [
    {"aud": ["some-other-access-app"]},          # a login for another app on the same team
    {"iss": "https://evil.cloudflareaccess.test"},
    {"exp": int(time.time()) - 600},              # expired
    {"email": None},                              # service token, not a person
    {"key": OTHER_KEY},                           # right key id, wrong key
    {"kid": "unknown"},
])
def test_bad_tokens_are_refused(client, bad):
    key, kid = bad.pop("key", TEAM_KEY), bad.pop("kid", "k1")
    assert client.get("/api/trends", headers=phone(token(key=key, kid=kid, **bad))).status_code == 403


def test_hs256_tokens_are_refused(client):
    # Algorithm confusion (HMAC instead of the team's RSA key): only RS256 is accepted.
    forged = jwt.api_jws.PyJWS().encode(
        json.dumps({"aud": [AUD], "iss": TEAM, "email": "me@example.test", "iat": int(time.time()),
                    "exp": int(time.time()) + 600}).encode(),
        b"x" * 32, algorithm="HS256", headers={"kid": "k1"})
    assert client.get("/api/trends", headers=phone(forged)).status_code == 403


def test_email_allow_list(client, monkeypatch):
    monkeypatch.setattr(config, "REMOTE_EMAILS", {"someone-else@example.test"})
    assert client.get("/api/trends", headers=phone(token())).status_code == 403
    monkeypatch.setattr(config, "REMOTE_EMAILS", {"me@example.test"})
    assert client.get("/api/trends", headers=phone(token(email="Me@Example.test"))).status_code == 200


def test_phone_writes_are_same_origin_only_and_audited(client):
    ok = phone(token(), origin=f"https://{HOST}")
    assert client.post("/api/trends", json=NOTE, headers=ok).status_code == 201
    assert client.post("/api/trends", json=NOTE, headers=phone(token())).status_code == 403  # no Origin
    assert client.post("/api/trends", json=NOTE, headers=phone(token(), origin="http://127.0.0.1:8765")).status_code == 403
    assert client.post("/api/trends", json=NOTE, headers=phone(token(), origin="https://evil.example")).status_code == 403
    assert client.post("/api/trends", json={"kind": "nope"}, headers=ok).status_code in (400, 422)

    log = client.get("/api/remote-edits").json()  # read locally
    assert log["enabled"] and log["host"] == HOST
    assert [(e["email"], e["method"], e["path"], e["status"] < 400) for e in log["edits"]] == [
        ("me@example.test", "POST", "/api/trends", False),
        ("me@example.test", "POST", "/api/trends", True),
    ]
    local = client.post("/api/trends", json=NOTE, headers={"origin": "http://127.0.0.1:8765"})
    assert local.status_code == 201 and len(client.get("/api/remote-edits").json()["edits"]) == 2  # local: not logged


def test_local_pages_cant_borrow_the_tunnel_origin(client):
    assert client.post("/api/trends", json=NOTE, headers={"origin": f"https://{HOST}"}).status_code == 403


def test_keys_are_cached_and_unknown_key_ids_cant_spam_cloudflare(client, certs):
    for _ in range(5):
        assert client.get("/api/trends", headers=phone(token())).status_code == 200
    assert len(certs.calls) == 1
    for i in range(20):
        assert client.get("/api/trends", headers=phone(token(kid=f"made-up-{i}"))).status_code == 403
    assert len(certs.calls) == remote.MAX_KEY_FETCHES_PER_HOUR
    assert client.get("/api/trends", headers=phone(token())).status_code == 200  # cached keys still work


def test_rotated_keys_are_picked_up(client, certs):
    assert client.get("/api/trends", headers=phone(token())).status_code == 200
    certs.replace(responses.GET, CERTS, json=_jwks(("k1", TEAM_KEY), ("k2", OTHER_KEY)))
    assert client.get("/api/trends", headers=phone(token(key=OTHER_KEY, kid="k2"))).status_code == 200


def test_cloudflare_down_means_refused_not_crashed(client, certs):
    certs.replace(responses.GET, CERTS, status=503)
    assert client.get("/api/trends", headers=phone(token())).status_code == 403
