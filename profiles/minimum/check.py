"""Exercise the public SDK with two independently issued tenant tokens."""
import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from client import get_tenant


def check(api, issuer, accounts):
    tenant = accounts["owner"]["tenant_id"]
    for name in ("owner", "other"):
        body = json.dumps({"username": name, "password": accounts[name]["password"]}).encode()
        request = Request(issuer + "/token", data=body, headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=10) as response:
            token = json.load(response)["access_token"]
        if name == "owner":
            assert get_tenant(api, tenant, token) == {"tenant_id": tenant}
        else:
            try:
                get_tenant(api, tenant, token)
            except HTTPError as error:
                if error.code != 403:
                    raise
            else:
                raise RuntimeError("Second tenant was granted access")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("api", "issuer", "accounts"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    check(args.api, args.issuer, json.loads(Path(args.accounts).read_text()))
