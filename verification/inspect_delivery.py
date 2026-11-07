"""Inspect one operator delivery without reading its contact address or code."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
from uuid import UUID


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("challenge_id", type=UUID)
    args = parser.parse_args()
    uri = Path(os.environ["VERIFICATION_DB"]).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT id AS challenge_id, dispatch, state, expires AS expires_at "
            "FROM challenge WHERE id=? AND method='operator-delivery'",
            (str(args.challenge_id),),
        ).fetchone()
    if row is None:
        parser.exit(1, "Operator delivery not found\n")
    print(json.dumps(dict(row)))


if __name__ == "__main__":
    main()
