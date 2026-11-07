"""Submit one delivery to an operator-owned Linux Unix-domain socket."""
import json
import os
import socket
import time

from . import store


def exchange(row):
    message = {"version": 1, "challenge_id": row["id"], "recipient": row["contact"],
               "expires_at": row["expires"], "code": store.delivery_code(store.key(), row["id"])}
    # One bounded packet per direction. Never redirect or log the packet.
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as peer:
        peer.settimeout(2)
        peer.connect(os.environ["VERIFICATION_ADAPTER_SOCKET"])
        packet = json.dumps(message).encode()
        if peer.send(packet) != len(packet):
            return "ambiguous"
        raw, _, flags, _ = peer.recvmsg(1024)
        if flags & socket.MSG_TRUNC:
            return "ambiguous"
        ack = json.loads(raw)
        if ack == {"version": 1, "challenge_id": row["id"], "status": "accepted"}:
            return "accepted"
        if ack == {"version": 1, "challenge_id": row["id"], "status": "rejected"}:
            return "rejected"
        return "ambiguous"


def dispatch_one():
    with store.transaction() as db:
        row = db.execute("""SELECT id, contact, expires FROM challenge WHERE method='operator-delivery'
                            AND dispatch='queued' AND state='pending' AND expires>? ORDER BY created LIMIT 1""",
                         (int(time.time()),)).fetchone()
        if not row:
            return False
        # Commit before I/O. Even a process death following acceptance cannot resend.
        db.execute("UPDATE challenge SET dispatch='claimed' WHERE id=?", (row["id"],))
    with store.transaction() as db:
        row = db.execute("""SELECT * FROM challenge WHERE id=? AND dispatch='claimed'
                            AND state='pending' AND expires>?""", (row["id"], int(time.time()))).fetchone()
        if not row:
            return True
        # Serialize contact changes with the bounded dispatch.
        try:
            status = exchange(row)
        except (OSError, ValueError, RecursionError):
            status = "ambiguous"
        db.execute("UPDATE challenge SET dispatch=?, delivered=? WHERE id=?",
                   (status, status == "accepted", row["id"]))
    return True
