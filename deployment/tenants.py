"""Render operator-owned tenant declarations for isolated realm and portal installations."""
import argparse
import base64
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import UUID


def identifier(value):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", value):
        raise ValueError("Invalid deployment identifier")
    return value


def https(value, origin=False):
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None
            or url.fragment or url.query or (origin and url.path) or any(c.isspace() for c in value)):
        raise ValueError("Expected an HTTPS origin or endpoint")
    return value


def render(catalog):
    outputs, routes = {}, {}
    origins, realms = set(), set()
    for tenant in catalog["tenants"]:
        uid = str(UUID(tenant["id"]))
        name = tenant["name"].strip()
        if uid in outputs or not name or len(name) > 100 or any(ord(c) < 32 or c in "<>" for c in name):
            raise ValueError("Invalid tenant identity or display name")
        overlay = identifier(tenant["plane_overlay"])
        peers = tenant["peers"]
        keys = set()
        for peer in peers:
            if set(peer) != {"url", "public_key"}:
                raise ValueError("Peer requires only its URL and public key")
            https(peer["url"])
            key = base64.b64decode(peer["public_key"], validate=True)
            if len(key) != 32 or key in keys:
                raise ValueError("Expected a unique 32-byte peer public key")
            keys.add(key)
        outputs[uid] = {"plane.json": {"tenant_id": uid, "overlay": overlay, "peers": peers}}
        login = tenant.get("login")
        if login is None:
            continue
        origin = https(login["portal_origin"], origin=True)
        realm = identifier(login["realm"])
        issuer = https(login["keycloak_origin"], origin=True) + "/realms/" + realm
        client = identifier(login["client_id"])
        if origin in origins or issuer in realms:
            raise ValueError("Duplicate portal origin or realm issuer")
        origins.add(origin)
        realms.add(issuer)
        picker = login.get("show_tenant_picker", False)
        if type(picker) is not bool:
            raise ValueError("Expected boolean show_tenant_picker")
        brokers = []
        for alias, registration in login.get("providers", {}).items():
            if alias not in {"google", "github", "microsoft"}:
                raise ValueError("Unsupported broker")
            if not registration.get("client_id") or not registration.get("secret_env"):
                continue
            ref = registration["secret_env"]
            if not re.fullmatch(r"[A-Z][A-Z0-9_]+", ref):
                raise ValueError("Invalid broker secret environment reference")
            config = {"clientId": registration["client_id"], "clientSecret": "${" + ref + "}"}
            provider = alias
            if alias == "microsoft":
                directory = str(UUID(registration["directory_id"]))
                base = "https://login.microsoftonline.com/" + directory
                provider = "oidc"
                config.update(authorizationUrl=base+"/oauth2/v2.0/authorize", tokenUrl=base+"/oauth2/v2.0/token",
                              issuer=base+"/v2.0", jwksUrl=base+"/discovery/v2.0/keys",
                              userInfoUrl="https://graph.microsoft.com/oidc/userinfo", validateSignature="true",
                              useJwksUrl="true", defaultScope="openid profile email", prompt="select_account")
            brokers.append(dict(alias=alias, providerId=provider, enabled=True, storeToken=False, config=config))
        routes[uid] = {"name": name, "url": origin + "/login"}
        outputs[uid]["realm.json"] = dict(realm=realm, enabled=True, displayName=name,
            loginTheme=login.get("theme", "keycloak.v2"), identityProviders=brokers, clients=[dict(
                clientId=client, name=name, enabled=True, protocol="openid-connect", publicClient=True,
                standardFlowEnabled=True, directAccessGrantsEnabled=False, webOrigins=[origin],
                redirectUris=[origin + "/api/auth/callback/keycloak"], attributes={"pkce.code.challenge.method":"S256"})])
        outputs[uid]["portal.json"] = dict(AUTH_URL=origin, AUTH_KEYCLOAK_ISSUER=issuer, AUTH_KEYCLOAK_ID=client,
            NEXT_PUBLIC_KEYCLOAK_ISSUER=issuer, NEXT_PUBLIC_AUTH_SCOPES_REQUIRED="true", NEXT_PUBLIC_LOGIN_TENANT_ID=uid,
            NEXT_PUBLIC_PORTAL_BRAND=name, NEXT_PUBLIC_TENANT_PICKER=str(picker).lower(),
            NEXT_PUBLIC_SSO_PROVIDERS=",".join(b["alias"] for b in brokers))
    for files in outputs.values():
        if "portal.json" in files:
            files["portal.json"]["PORTAL_TENANT_ROUTES"] = json.dumps(routes)
    return outputs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    artifacts = render(json.loads(args.catalog.read_text()))
    args.output.mkdir(exist_ok=False)
    for uid, files in artifacts.items():
        directory = args.output / uid
        directory.mkdir()
        for filename, value in files.items():
            (directory / filename).write_text(json.dumps(value, indent=2) + "\n")
