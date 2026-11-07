"""Synthetic CI adapter. Only test addresses are permitted."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import time

path = Path(os.environ["VERIFICATION_ADAPTER_SOCKET"])
if sys.argv[1:] == ["ready"]:
    sys.exit(0 if path.with_suffix(".ready").exists() else 1)
with sqlite3.connect("/adapter/test-receipts.db") as db:
    db.execute("CREATE TABLE IF NOT EXISTS receipts(id TEXT, code TEXT)")
    path.with_suffix(".ready").unlink(missing_ok=True)
    path.unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as server:
        server.bind(str(path))
        server.listen()
        path.with_suffix(".ready").touch()
        while True:
            peer, _ = server.accept()
            with peer:
                msg = json.loads(peer.recv(8192))
                assert msg["recipient"].endswith("@sample.test")
                db.execute("INSERT INTO receipts VALUES (?,?)", (msg["challenge_id"], msg["code"]))
                db.commit()
                action = msg["recipient"].split("@")[0]
                if action == "lost":
                    continue
                if action == "timeout":
                    time.sleep(3)
                    continue
                ack = {"version": 1, "challenge_id": msg["challenge_id"],
                       "status": "rejected" if action == "reject" else "accepted"}
                peer.sendall(json.dumps(ack).encode())
