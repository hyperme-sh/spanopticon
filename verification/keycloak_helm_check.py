"""Exercise the installed API with disposable Keycloak service-account tokens."""
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import uuid4

from verification.qualify import request


def service_tokens(cfg, issuer):
    tokens = {}
    for name, secret in json.loads((cfg / "credentials.json").read_text()).items():
        form = urlencode({"grant_type": "client_credentials", "client_id": "minimum-" + name,
                          "client_secret": secret}).encode()
        with urlopen(issuer + "/protocol/openid-connect/token", data=form, timeout=10) as response:
            tokens[name] = json.load(response)["access_token"]

    return tokens


def mailbox_code(cid):
    query = ("import sqlite3, sys; db=sqlite3.connect('/mailbox/inbox.db'); "
             "row=db.execute('SELECT code FROM message WHERE id=?',(sys.argv[1],)).fetchone(); "
             "print(row[0] if row else '')")
    for _ in range(30):
        code = subprocess.check_output(["kubectl", "exec", "deployment/contact", "-c", "worker", "--",
                                        "python", "-c", query, cid], text=True, timeout=15).strip()
        if code:
            return code
        time.sleep(1)
    raise RuntimeError("Local delivery timed out")


def main(cfg, issuer, api, restored=False):
    tokens = service_tokens(cfg, issuer)
    if restored:
        cid = (cfg / "restore-challenge-id").read_text()
        assert request(api + "/v1/evidence", token=tokens["owner"])["verified"]
        assert request(api + "/v1/evidence", token=tokens["other"])["verified"]
        request(api + "/v1/challenges/" + cid + "/complete", token=tokens["owner"],
                body={"code": mailbox_code(cid)}, expected=400)
        return
    challenges = api + "/v1/challenges"
    params = {"body": {"address": "operator@example.test"}, "key": str(uuid4())}
    request(challenges, token=tokens["wrong-audience"], expected=401, **params)
    owner = tokens["owner"]
    challenge = request(challenges, token=owner, **params)
    assert request(challenges, token=owner, **params) == challenge
    cid = challenge["challenge_id"]
    code = mailbox_code(cid)
    complete = f"{challenges}/{cid}/complete"
    answer = {"code": code}
    request(complete, token=tokens["other"], body=answer, expected=400)
    request(complete, token=owner, body=answer)
    request(complete, token=owner, body=answer, expected=400)
    (cfg / "restore-challenge-id").write_text(cid)
    assert request(api + "/v1/evidence", token=owner)["verified"]
    assert not request(api + "/v1/evidence", token=tokens["other"])["verified"]
    other_id = request(challenges, token=tokens["other"], body={"address": "peer@example.test"},
                       key=str(uuid4()))["challenge_id"]
    request(f"{challenges}/{other_id}/complete", token=tokens["other"], body={"code": mailbox_code(other_id)})



if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4:] == ["--restored"])
