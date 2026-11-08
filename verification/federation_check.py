"""Run an isolated two-realm OIDC brokering drill on a designated TEST Keycloak."""
import argparse
import base64
from contextlib import ExitStack
import hashlib
from html.parser import HTMLParser
import secrets
from urllib.parse import parse_qs, urljoin, urlsplit
from uuid import uuid4

import requests


class LoginForm(HTMLParser):
    action = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == "kc-form-login":
            self.action = attrs["action"]


def require(response, status):
    if response.status_code != status:
        raise RuntimeError(f"Expected HTTP {status}, received {response.status_code}")
    return response


def login(origin, broker, upstream, callback, username, password):
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(24)
    params = dict(client_id="test-browser", response_type="code", scope="openid", state=state,
                  redirect_uri=callback, kc_idp_hint="test-upstream", code_challenge_method="S256",
                  code_challenge=challenge)
    oidc = f"{origin}/realms/{broker}/protocol/openid-connect"
    with requests.Session() as browser:
        response = browser.get(oidc + "/auth", params=params, timeout=15, allow_redirects=False)
        authenticated = False
        for _ in range(24):
            if response.is_redirect:
                target = urljoin(response.url, response.headers["Location"])
                if target.partition("?")[0] == callback:
                    query = parse_qs(urlsplit(target).query)
                    assert authenticated and query["state"] == [state]
                    break
                if not target.startswith(origin + "/"):
                    raise RuntimeError("Unexpected redirect origin")
                response = browser.get(target, timeout=15, allow_redirects=False)
                continue
            require(response, 200)
            form = LoginForm()
            form.feed(response.text)
            if authenticated or not form.action or not form.action.startswith(f"{origin}/realms/{upstream}/login-actions/"):
                raise RuntimeError("Expected a single login form on the temporary upstream realm")
            response = browser.post(form.action, data=dict(username=username, password=password),
                                    timeout=15, allow_redirects=False)
            authenticated = True
        else:
            raise RuntimeError("Federated login exceeded redirect limit")
        token = require(browser.post(oidc + "/token", data=dict(grant_type="authorization_code",
                        client_id="test-browser", code=query["code"][0], code_verifier=verifier,
                        redirect_uri=callback), timeout=15), 200).json()["access_token"]
        headers = {"Authorization": "Bearer " + token}
        me = require(browser.get(oidc + "/userinfo", headers=headers, timeout=15), 200).json()
        assert me["preferred_username"] == username
        require(browser.get(f"{origin}/realms/{upstream}/protocol/openid-connect/userinfo",
                            headers=headers, timeout=15), 401)
        return me["sub"]


def run(origin, admin_origin, admin_token):
    suffix = uuid4().hex[:12]
    upstream, broker = "test-upstream-" + suffix, "test-broker-" + suffix
    username = "synthetic-" + suffix
    password, secret = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    callback = "http://127.0.0.1:8765/callback"
    upstream_oidc = f"{origin}/realms/{upstream}/protocol/openid-connect"
    broker_callback = f"{origin}/realms/{broker}/broker/test-upstream/endpoint"
    with requests.Session() as admin:
        admin.headers["Authorization"] = "Bearer " + admin_token
        realms = admin_origin + "/admin/realms"
        def remove(name):
            require(admin.delete(realms + "/" + name, timeout=30), 204)
            require(admin.get(realms + "/" + name, timeout=15), 404)

        with ExitStack() as cleanup:
            def create(name, **config):
                require(admin.post(realms, json=dict(realm=name, enabled=True, **config), timeout=45), 201)
                cleanup.callback(remove, name)

            create(upstream, clients=[dict(clientId="broker", secret=secret, protocol="openid-connect",
                   redirectUris=[broker_callback], standardFlowEnabled=True, directAccessGrantsEnabled=False)],
                   users=[dict(username=username, enabled=True, firstName="Synthetic", lastName="Fixture",
                   email=username+"@example.test", emailVerified=True,
                   credentials=[dict(type="password", value=password, temporary=False)])])
            create(broker, clients=[dict(clientId="test-browser", protocol="openid-connect", publicClient=True,
                   redirectUris=[callback], standardFlowEnabled=True, directAccessGrantsEnabled=False,
                   attributes={"pkce.code.challenge.method": "S256"})],
                   identityProviders=[dict(alias="test-upstream", providerId="oidc", enabled=True,
                   config=dict(clientId="broker", clientSecret=secret, issuer=f"{origin}/realms/{upstream}",
                   authorizationUrl=upstream_oidc+"/auth", tokenUrl=upstream_oidc+"/token",
                   userInfoUrl=upstream_oidc+"/userinfo", validateSignature="true", useJwksUrl="true",
                   jwksUrl=upstream_oidc+"/certs", defaultScope="openid profile email"))])
            first = login(origin, broker, upstream, callback, username, password)
            assert login(origin, broker, upstream, callback, username, password) == first
            links = require(admin.get(f"{realms}/{broker}/users/{first}/federated-identity", timeout=15), 200).json()
            users = require(admin.get(f"{realms}/{upstream}/users", params={"username":username,"exact":"true"},
                                      timeout=15), 200).json()
            assert len(users) == len(links) == 1
            assert links[0]["identityProvider"] == "test-upstream" and links[0]["userId"] == users[0]["id"]
    print(f"Federated PKCE login twice, stable subject, upstream link and foreign-token rejection passed. Removed {broker} and {upstream}.")


if __name__ == "__main__":
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--admin-origin", required=True)
    args = parser.parse_args()
    run(args.origin.rstrip("/"), args.admin_origin.rstrip("/"), sys.stdin.readline().strip())
