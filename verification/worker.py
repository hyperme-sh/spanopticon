"""Deliver only to a worker-private SQLite test mailbox; no network transport."""
import os
import time

from . import store


def deliver_one():
    with store.transaction() as db:
        db.execute("ATTACH DATABASE ? AS inbox", (os.environ["VERIFICATION_MAILBOX"],))
        db.execute("""CREATE TABLE IF NOT EXISTS inbox.message
                      (id TEXT PRIMARY KEY, address TEXT NOT NULL, code TEXT NOT NULL, expires INTEGER NOT NULL)""")
        now = int(time.time())
        db.execute("DELETE FROM inbox.message WHERE expires<=?", (now,))
        row = db.execute("""SELECT id, contact, expires FROM challenge
                            WHERE delivered=0 AND state='pending' AND expires>? ORDER BY created LIMIT 1""",
                         (now,)).fetchone()
        if not row:
            return False
        db.execute("INSERT INTO inbox.message VALUES (?,?,?,?)",
                   (row["id"], row["contact"], store.delivery_code(store.key(), row["id"]), row["expires"]))
        db.execute("UPDATE challenge SET delivered=1 WHERE id=?", (row["id"],))
        return True


if __name__ == "__main__":
    os.umask(0o077)
    store.ready()
    while True:
        if not deliver_one():
            time.sleep(1)
