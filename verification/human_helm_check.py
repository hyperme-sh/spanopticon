"""Qualify human PKCE login and logout in the disposable local Helm realm."""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import sys
import time
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen
from uuid import uuid4

from verification.keycloak_helm_check import mailbox_code, service_tokens
from verification.qualify import request

CLIENT = "verification-human-drill"
CALLBACK = "http://127.0.0.1:8765/callback"


def prepare(cfg):
    path = cfg / "realm.json"
    realm = json.loads(path.read_text())
    password = secrets.token_urlsafe(32)
    realm["users"].append({"username": "human-drill", "enabled": True, "firstName": "Test", "lastName": "User",
                           "email": "human@example.test", "emailVerified": True,
                           "credentials": [{"type": "password", "value": password, "temporary": False}]})
    owner = next(c for c in realm["clients"] if c["clientId"] == "minimum-owner")
    realm["clients"].append({"clientId": CLIENT, "enabled": True, "publicClient": True,
                             "protocol": "openid-connect", "standardFlowEnabled": True,
                             "directAccessGrantsEnabled": False, "redirectUris": [CALLBACK],
                             "attributes": {"pkce.code.challenge.method": "S256", "access.token.lifespan": "30"},
                             "protocolMappers": owner["protocolMappers"]})
    replacement = cfg / "human-realm.tmp"
    replacement.write_text(json.dumps(realm))
    replacement.chmod(0o444)
    replacement.replace(path)
    (cfg / "human-password").write_text(password)


def post(url, fields):
    return urlopen(url, data=urlencode(fields).encode(), timeout=10)


class Callback(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if urlsplit(self.path).path == "/callback" else 404)
        self.end_headers()

    def log_message(self, *args):
        # The query contains a one-time authorization code.
        pass


def check(cfg, issuer, api, retry=False):
    from playwright.sync_api import sync_playwright

    oidc = issuer + "/protocol/openid-connect"
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(24)
    fields = {"client_id": CLIENT, "redirect_uri": CALLBACK, "response_type": "code", "scope": "openid",
              "code_challenge_method": "S256", "code_challenge": challenge, "state": state}
    peers = service_tokens(cfg, issuer)
    with ThreadingHTTPServer(("127.0.0.1", 8765), Callback) as server, sync_playwright() as pw:
        Thread(target=server.serve_forever, daemon=True).start()
        browser = pw.chromium.launch(channel="chrome")
        page = browser.new_page()
        page.goto(oidc + "/auth?" + urlencode(fields))
        page.locator("#username").fill("human-drill")
        page.locator("#password").fill((cfg / "human-password").read_text())
        page.locator("#kc-login").click()
        page.wait_for_url(lambda url: url.startswith(CALLBACK + "?"))
        callback = parse_qs(urlsplit(page.url).query)
        assert callback["state"] == [state]
        with post(oidc + "/token", {"grant_type": "authorization_code", "code": callback["code"][0],
                                    "code_verifier": verifier, "client_id": CLIENT, "redirect_uri": CALLBACK}) as res:
            tokens = json.load(res)
        token = tokens["access_token"]
        expiry = time.monotonic() + tokens["expires_in"]
        assert 0 < tokens["expires_in"] <= 30
        evidence_url = api + "/v1/evidence"
        receipt = cfg / "human-evidence.json"
        if retry:
            evidence = request(evidence_url, token=token)
            assert evidence["verified"] and evidence == json.loads(receipt.read_text())
            browser.close()
            server.shutdown()
            return
        assert not request(evidence_url, token=token)["verified"]
        challenges = api + "/v1/challenges"
        cid = request(challenges, token=token, body={"address": "human@example.test"},
                      key=str(uuid4()))["challenge_id"]
        answer = {"code": mailbox_code(cid)}
        for peer in ("owner", "other"):
            request(f"{challenges}/{cid}/complete", token=peers[peer], body=answer, expected=400)
        request(f"{challenges}/{cid}/complete", token=token, body=answer)
        evidence = request(evidence_url, token=token)
        assert evidence["verified"]
        receipt.write_text(json.dumps(evidence))
        with post(oidc + "/logout", {"client_id": CLIENT, "refresh_token": tokens["refresh_token"]}) as res:
            assert res.status == 204
        try:
            with post(oidc + "/token", {"grant_type": "refresh_token", "client_id": CLIENT,
                                        "refresh_token": tokens["refresh_token"]}):
                raise AssertionError("Logged-out refresh token was accepted")
        except HTTPError as exc:
            assert exc.code == 400 and json.load(exc)["error"] == "invalid_grant"
        silent = fields | {"prompt": "none", "state": secrets.token_urlsafe(24)}
        page.goto(oidc + "/auth?" + urlencode(silent))
        page.wait_for_url(lambda url: url.startswith(CALLBACK + "?"))
        result = parse_qs(urlsplit(page.url).query)
        assert result["state"] == [silent["state"]] and result["error"] == ["login_required"]
        browser.close()
        server.shutdown()
    time.sleep(max(0, expiry - time.monotonic()) + 1)
    request(api + "/v1/evidence", token=token, expected=401)


if __name__ == "__main__":
    if sys.argv[1] == "prepare":
        prepare(Path(sys.argv[2]))
    else:
        check(Path(sys.argv[2]), sys.argv[3], sys.argv[4], retry=sys.argv[1] == "retry")
