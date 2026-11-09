"""Exercise the verifier's single-key trust cutover in the disposable Helm realm."""
import base64
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from verification.keycloak_helm_check import service_tokens
from verification.qualify import request


def token_field(token, part, field):
    data = token.split(".")[part]
    return json.loads(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))[field]


def rotate(issuer):
    origin, realm = issuer.rsplit("/realms/", 1)
    form = urlencode({"grant_type": "password", "client_id": "admin-cli", "username": "rollover-admin",
                      "password": os.environ["KC_BOOTSTRAP_ADMIN_PASSWORD"]}).encode()
    with urlopen(origin + "/realms/master/protocol/openid-connect/token", data=form, timeout=10) as response:
        headers = {"Authorization": "Bearer " + json.load(response)["access_token"]}
    endpoint = origin + "/admin/realms/" + realm
    with urlopen(Request(endpoint, headers=headers), timeout=10) as response:
        realm_id = json.load(response)["id"]
    component = {"name": "rollover-test", "providerId": "rsa-generated", "parentId": realm_id,
                 "providerType": "org.keycloak.keys.KeyProvider",
                 "config": {"priority": ["200"], "enabled": ["true"], "active": ["true"],
                            "algorithm": ["RS256"], "keySize": ["2048"]}}
    with urlopen(Request(endpoint + "/components", data=json.dumps(component).encode(),
                         headers={**headers, "Content-Type": "application/json"}), timeout=15) as response:
        assert response.status == 201


def main(stage, cfg, issuer, api):
    evidence = api + "/v1/evidence"
    old_path = cfg / "rollover-old.jwt"
    if stage == "rotate":
        old = service_tokens(cfg, issuer)["other"]
        assert request(evidence, token=old)["verified"]
        old_path.write_text(old)
        rotate(issuer)
        new = service_tokens(cfg, issuer)["other"]
        assert token_field(old, 0, "kid") != token_field(new, 0, "kid")
        request(evidence, token=new, expected=401)
        assert request(evidence, token=old)["verified"]
    elif stage == "verify":
        old = old_path.read_text()
        tokens = service_tokens(cfg, issuer)
        assert request(evidence, token=tokens["other"])["verified"]
        request(evidence, token=tokens["wrong-audience"], expected=401)
        request(evidence, token=old, expected=401)
        assert time.time() < token_field(old, 1, "exp"), "Old token expired during the drill"
        old_path.unlink()
    else:
        raise ValueError("Unknown rollover stage")


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4])
