"""Exercise the minimum external issuer contract with a disposable Keycloak realm."""
import argparse
import json
import secrets
import textwrap
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import uuid4

from client import get_tenant


def prepare(directory):
    directory.mkdir(mode=0o700)
    tenant = str(uuid4())
    clients, users, credentials = [], [], {}
    for name in ("owner", "other", "wrong-audience"):
        secret = secrets.token_urlsafe(32)
        credentials[name] = secret
        client_id = f"minimum-{name}"
        users.append({"username": f"service-account-{client_id}", "enabled": True,
                      "serviceAccountClientId": client_id})
        clients.append({
            "clientId": client_id, "protocol": "openid-connect", "enabled": True,
            "publicClient": False, "secret": secret, "serviceAccountsEnabled": True,
            "standardFlowEnabled": False, "directAccessGrantsEnabled": False,
            "protocolMappers": [
                {"name": "tenant", "protocol": "openid-connect", "protocolMapper": "oidc-hardcoded-claim-mapper",
                 "config": {"claim.name": "tenant_id", "claim.value": str(uuid4()) if name == "other" else tenant,
                            "jsonType.label": "String", "access.token.claim": "true"}},
                {"name": "audience", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
                 "config": {"included.custom.audience": "other-api" if name == "wrong-audience" else "minimum-api",
                            "access.token.claim": "true"}}]})
    realm = directory / "realm.json"
    realm.write_text(json.dumps({"realm": "minimum", "enabled": True, "sslRequired": "none",
                                 "clients": clients, "users": users}))
    realm.chmod(0o444)  # Keycloak reads this single bind mount inside the disposable container.
    (directory / "credentials.json").write_text(json.dumps(credentials))
    (directory / "tenant").write_text(tenant)
    (directory / "compose.env").write_text(f"MINIMUM_CONFIG={directory.resolve()}\nMINIMUM_TENANT={tenant}\n")


def trust(directory, issuer):
    with urlopen(issuer, timeout=10) as response:
        key = json.load(response)["public_key"]
    (directory / "issuer.pub").write_text("-----BEGIN PUBLIC KEY-----\n" +
        "\n".join(textwrap.wrap(key, 64)) + "\n-----END PUBLIC KEY-----\n")


def check(directory, issuer, api):
    tenant = (directory / "tenant").read_text()
    for name, secret in json.loads((directory / "credentials.json").read_text()).items():
        data = urlencode({"grant_type": "client_credentials", "client_id": "minimum-" + name,
                          "client_secret": secret}).encode()
        with urlopen(issuer + "/protocol/openid-connect/token", data=data, timeout=10) as response:
            token = json.load(response)["access_token"]
        expected = {"owner": 200, "other": 403, "wrong-audience": 401}[name]
        try:
            result = get_tenant(api, tenant, token)
        except HTTPError as error:
            if error.code != expected:
                raise
        else:
            if expected != 200 or result != {"tenant_id": tenant}:
                raise RuntimeError("Unexpected tenant access")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "trust", "check"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--issuer")
    parser.add_argument("--api")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.directory)
    elif args.action == "trust":
        trust(args.directory, args.issuer)
    else:
        check(args.directory, args.issuer, args.api)
