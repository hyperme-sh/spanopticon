"""Offline deployment inputs: migrate and provision declared memberships."""

import argparse
import json
import os
from pathlib import Path
from uuid import UUID
import psycopg
from psycopg.rows import dict_row


def migrate(db):
    db.execute("SELECT pg_advisory_xact_lock(hashtextextended('feedback:migration',0))")
    if db.execute("SELECT to_regclass('feedback.schema_version') AS name").fetchone()[
        "name"
    ]:
        if (
            db.execute("SELECT version FROM feedback.schema_version").fetchone()[
                "version"
            ]
            != 1
        ):
            raise ValueError("Unsupported intake schema")
        return
    db.execute(Path(__file__).with_name("migrations").joinpath("001.sql").read_text())


def members(db, records):
    for row in records:
        tenant = str(UUID(row["tenant"]))
        if (
            row["role"] not in ("submitter", "reviewer")
            or type(row["enabled"]) is not bool
        ):
            raise ValueError("Invalid membership")
        if any(
            not isinstance(row[k], str) or not 1 <= len(row[k]) <= 200
            for k in ("issuer", "subject")
        ):
            raise ValueError("Invalid principal")
        db.execute("SELECT set_config('feedback.tenant',%s,true)", (tenant,))
        db.execute(
            """INSERT INTO feedback.member(tenant,issuer,subject,role,enabled) VALUES(%s,%s,%s,%s,%s)
            ON CONFLICT(tenant,issuer,subject) DO UPDATE SET role=excluded.role,enabled=excluded.enabled""",
            (tenant, row["issuer"], row["subject"], row["role"], row["enabled"]),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["migrate", "members"])
    parser.add_argument("--file")
    args = parser.parse_args()
    with psycopg.connect(
        Path(os.environ["INTAKE_ADMIN_DATABASE_URL_FILE"]).read_text().strip(),
        row_factory=dict_row,
    ) as db:
        if args.operation == "migrate":
            migrate(db)
        else:
            members(db, json.loads(Path(args.file).read_text()))
