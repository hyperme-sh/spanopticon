"""Invalidate rollback-sensitive proofs on an isolated restored verifier database."""
from . import store


def invalidate_snapshot_proofs():
    with store.transaction() as db:
        if db.execute("PRAGMA user_version").fetchone()[0] != 2:
            raise RuntimeError("Migrate the restored verification database before recovery")
        # The provider may already have acted on a row still queued in the snapshot.
        db.execute("UPDATE challenge SET state='superseded' WHERE state='pending'")
        db.execute("UPDATE contact SET verified_until=0 WHERE verified_until<>0")


if __name__ == "__main__":
    invalidate_snapshot_proofs()
