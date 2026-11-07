"""Record an offline erasure or reconcile restored verification volumes."""
import argparse
import os
from uuid import UUID

from . import authority, store


def replay():
    _, scopes = authority.read(os.environ["VERIFICATION_KEY_FILE"])
    with store.transaction() as db:
        if db.execute("PRAGMA user_version").fetchone()[0] != 2:
            raise ValueError("Expected verification schema 2")
        local = store.delivery_method() == "local-test-delivery"
        if local:
            db.execute("ATTACH DATABASE ? AS inbox", (os.environ["VERIFICATION_MAILBOX"],))
        for scope in scopes:
            db.execute("DELETE FROM challenge WHERE scope=?", (scope,))
            db.execute("DELETE FROM contact WHERE scope=?", (scope,))
        if local and db.execute("SELECT 1 FROM inbox.sqlite_master WHERE type='table' AND name='message'").fetchone():
            db.execute("DELETE FROM inbox.message WHERE NOT EXISTS (SELECT 1 FROM main.challenge WHERE challenge.id=message.id)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("record")
    add.add_argument("--authority", required=True)
    add.add_argument("--issuer", required=True)
    add.add_argument("--tenant", required=True, type=UUID)
    add.add_argument("--subject", required=True)
    commands.add_parser("replay")
    args = parser.parse_args()
    if args.command == "record":
        authority.record(args.authority, store.principal_scope(args.issuer, str(args.tenant), args.subject))
    else:
        replay()


if __name__ == "__main__":
    main()
