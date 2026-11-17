"""PostgreSQL authority. Every operation has a tenant and a scoped transaction."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from . import policy


@dataclass(frozen=True)
class Principal:
    tenant: str
    issuer: str
    subject: str
    device: str


class Refusal(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail


class Store:
    CONSENT_SUBJECT_LIMIT = 2048
    CONSENT_TENANT_LIMIT = 65536
    CONSENT_SUBJECT_HOURLY = 20
    CONSENT_TENANT_HOURLY = 1000

    def __init__(self, dsn):
        self.dsn = dsn

    @contextmanager
    def transaction(self, tenant):
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as db:
            db.execute("SELECT set_config('feedback.tenant',%s,true)", (str(tenant),))
            db.execute("SET LOCAL statement_timeout='15s'")
            db.execute("SET LOCAL lock_timeout='5s'")
            # One order for consent, ingestion, review, publishing and erasure.
            # This also serializes tenant deduplication and rate budgets.
            db.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("feedback:" + str(tenant),),
            )
            yield db

    def ready(self):
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as db:
            role = db.execute(
                "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
            if role["rolsuper"] or role["rolbypassrls"]:
                raise RuntimeError("Intake runtime must enforce row security")
            if (
                db.execute("SELECT version FROM feedback.schema_version").fetchone()[
                    "version"
                ]
                != 1
            ):
                raise RuntimeError("Unsupported intake schema")
            tables = db.execute(
                "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class WHERE relnamespace='feedback'::regnamespace AND relkind='r' AND relname<>'schema_version'"
            ).fetchall()
            if {r["relname"] for r in tables} != {
                "member",
                "consent",
                "consent_history",
                "report",
                "decision",
                "erasure",
            } or not all(
                r["relrowsecurity"] and r["relforcerowsecurity"] for r in tables
            ):
                raise RuntimeError("Intake migration required")

    def member(self, db, p, reviewer=False, rights=False, submitter=False):
        row = db.execute(
            "SELECT role,enabled FROM feedback.member WHERE tenant=%s AND issuer=%s AND subject=%s",
            (p.tenant, p.issuer, p.subject),
        ).fetchone()
        if (
            not row
            or (not rights and not row["enabled"])
            or (reviewer and row["role"] != "reviewer")
            or (submitter and row["role"] != "submitter")
        ):
            raise Refusal(403, "Intake membership required")
        return row

    def consent(self, p, body=None):
        with self.transaction(p.tenant) as db:
            self.member(db, p, submitter=True)
            args = (p.tenant, p.issuer, p.subject, p.device)
            row = db.execute(
                "SELECT * FROM feedback.consent WHERE tenant=%s AND issuer=%s AND subject=%s AND device=%s",
                args,
            ).fetchone()
            revision = row["revision"] if row else 0
            if body is None:
                return {
                    "enabled": bool(row and row["enabled"]),
                    "revision": revision,
                    "notice": policy.NOTICE,
                    "purpose": policy.PURPOSE,
                }
            if body.expected_revision != revision:
                raise Refusal(409, "Consent revision changed")
            if (
                body.enabled
                and db.execute(
                    "SELECT 1 FROM feedback.erasure WHERE tenant=%s AND issuer=%s AND subject=%s",
                    args[:3],
                ).fetchone()
            ):
                raise Refusal(409, "Deletion blocks new intake")
            if body.enabled == bool(row and row["enabled"]):
                return {
                    "enabled": body.enabled,
                    "revision": revision,
                    "notice": body.notice,
                    "purpose": body.purpose,
                }
            if body.enabled:
                # Reserve a history slot for withdrawal of every active grant.
                # Withdrawals never consume more of this combined budget and
                # remain available even when opt-in limits are exhausted.
                for scope, params, limit, hourly in (
                    (
                        "tenant=%s AND issuer=%s AND subject=%s",
                        args[:3],
                        self.CONSENT_SUBJECT_LIMIT,
                        self.CONSENT_SUBJECT_HOURLY,
                    ),
                    (
                        "tenant=%s",
                        args[:1],
                        self.CONSENT_TENANT_LIMIT,
                        self.CONSENT_TENANT_HOURLY,
                    ),
                ):
                    counts = db.execute(
                        "SELECT count(*) AS total, count(*) FILTER (WHERE enabled AND changed_at>now()-interval '1 hour') AS recent FROM feedback.consent_history WHERE "
                        + scope,
                        params,
                    ).fetchone()
                    active = db.execute(
                        "SELECT count(*) AS n FROM feedback.consent WHERE enabled AND "
                        + scope,
                        params,
                    ).fetchone()["n"]
                    if (
                        counts["total"] + active + 2 > limit
                        or counts["recent"] >= hourly
                    ):
                        raise Refusal(
                            429,
                            "Consent opt-in limit reached; withdrawal remains available",
                        )
            db.execute(
                """INSERT INTO feedback.consent(tenant,issuer,subject,device,revision,enabled,notice,purpose)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant,issuer,subject,device)
                DO UPDATE SET revision=excluded.revision,enabled=excluded.enabled,notice=excluded.notice,purpose=excluded.purpose,changed_at=now()""",
                (*args, revision + 1, body.enabled, body.notice, body.purpose),
            )
            db.execute(
                "UPDATE feedback.consent_history SET expires_at=now()+interval '24 months' WHERE tenant=%s AND issuer=%s AND subject=%s AND device=%s AND expires_at IS NULL",
                args,
            )
            db.execute(
                """INSERT INTO feedback.consent_history(tenant,issuer,subject,device,revision,enabled,notice,purpose,expires_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s THEN NULL ELSE now()+interval '24 months' END)""",
                (
                    *args,
                    revision + 1,
                    body.enabled,
                    body.notice,
                    body.purpose,
                    body.enabled,
                ),
            )
            return {
                "enabled": body.enabled,
                "revision": revision + 1,
                "notice": body.notice,
                "purpose": body.purpose,
            }

    @staticmethod
    def view(row):
        return {
            k: row[k]
            for k in (
                "id",
                "category",
                "component",
                "title",
                "body",
                "state",
                "created_at",
                "expires_at",
                "closed_at",
                "issue_number",
                "external_pending",
            )
        }

    def submit(self, p, request_id, body, repository):
        with self.transaction(p.tenant) as db:
            self.member(db, p, submitter=True)
            scope = (p.tenant, p.issuer, p.subject, p.device)
            consent = db.execute(
                "SELECT revision,enabled FROM feedback.consent WHERE tenant=%s AND issuer=%s AND subject=%s AND device=%s",
                scope,
            ).fetchone()
            if (
                not consent
                or not consent["enabled"]
                or consent["revision"] != body.consent_revision
            ):
                raise Refusal(409, "Current human-report consent required")
            if db.execute(
                "SELECT 1 FROM feedback.erasure WHERE tenant=%s AND issuer=%s AND subject=%s",
                scope[:3],
            ).fetchone():
                raise Refusal(409, "Deletion blocks new intake")
            title, text, fingerprint, spam = policy.prepare(body)
            prior = db.execute(
                "SELECT * FROM feedback.report WHERE tenant=%s AND issuer=%s AND subject=%s AND device=%s AND request_id=%s",
                (*scope, request_id),
            ).fetchone()
            if prior:
                if (
                    prior["fingerprint"] != fingerprint
                    or prior["state"] == "deleted"
                    or prior["expires_at"] <= datetime.now(timezone.utc)
                ):
                    raise Refusal(409, "Request key already used")
                return self.view(prior)
            count = db.execute(
                "SELECT count(*) AS n FROM feedback.report WHERE tenant=%s AND issuer=%s AND subject=%s AND created_at>now()-interval '1 hour'",
                scope[:3],
            ).fetchone()["n"]
            tenant_count = db.execute(
                "SELECT count(*) AS n FROM feedback.report WHERE tenant=%s AND created_at>now()-interval '1 hour'",
                scope[:1],
            ).fetchone()["n"]
            if count >= 10 or tenant_count >= 1000:
                raise Refusal(429, "Submission limit reached")
            duplicate = db.execute(
                "SELECT 1 FROM feedback.report WHERE tenant=%s AND fingerprint=%s AND state NOT IN ('deleted','spam','rejected') AND expires_at>now() LIMIT 1",
                (p.tenant, fingerprint),
            ).fetchone()
            state = "spam" if spam else "duplicate" if duplicate else "pending"
            days = 7 if state in ("spam", "duplicate") else 180
            row = db.execute(
                """INSERT INTO feedback.report(tenant,id,issuer,subject,device,request_id,consent_revision,category,component,repository,title,body,fingerprint,state,expires_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now()+make_interval(days=>%s)) RETURNING *""",
                (
                    p.tenant,
                    uuid4(),
                    p.issuer,
                    p.subject,
                    p.device,
                    request_id,
                    body.consent_revision,
                    body.category,
                    body.component,
                    repository,
                    title,
                    text,
                    fingerprint,
                    state,
                    days,
                ),
            ).fetchone()
            return self.view(row)

    def reports(self, p, review=False, after=None):
        with self.transaction(p.tenant) as db:
            self.member(db, p, reviewer=review, rights=not review)
            if review:
                rows = db.execute(
                    """SELECT * FROM feedback.report r WHERE tenant=%s AND state<>'deleted' AND expires_at>now()
                    AND id>coalesce(%s::uuid,'00000000-0000-0000-0000-000000000000'::uuid)
                    AND NOT EXISTS(SELECT 1 FROM feedback.erasure e WHERE e.tenant=r.tenant AND e.issuer=r.issuer AND e.subject=r.subject)
                    ORDER BY id LIMIT 100""",
                    (p.tenant, after),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM feedback.report r WHERE tenant=%s AND issuer=%s AND subject=%s AND state<>'deleted' AND expires_at>now() AND NOT EXISTS(SELECT 1 FROM feedback.erasure e WHERE e.tenant=r.tenant AND e.issuer=r.issuer AND e.subject=r.subject) ORDER BY created_at,id LIMIT 1000",
                    (p.tenant, p.issuer, p.subject),
                ).fetchall()
            return [self.view(r) for r in rows]

    def get(self, db, p, rid):
        row = db.execute(
            "SELECT * FROM feedback.report WHERE tenant=%s AND id=%s FOR UPDATE",
            (p.tenant, rid),
        ).fetchone()
        if not row:
            raise Refusal(404, "Report not found")
        if row["state"] != "deleted" and row["expires_at"] <= datetime.now(
            timezone.utc
        ):
            raise Refusal(409, "Report expired")
        if (
            row["state"] != "deleted"
            and db.execute(
                "SELECT 1 FROM feedback.erasure WHERE tenant=%s AND issuer=%s AND subject=%s",
                (p.tenant, row["issuer"], row["subject"]),
            ).fetchone()
        ):
            raise Refusal(409, "Deletion blocks this report")
        return row

    def decide(self, p, rid, body):
        with self.transaction(p.tenant) as db:
            self.member(db, p, reviewer=True)
            row = self.get(db, p, rid)
            if row["state"] != "pending" or row["expires_at"] <= datetime.now(
                timezone.utc
            ):
                raise Refusal(409, "Report is not awaiting review")
            db.execute(
                "INSERT INTO feedback.decision(tenant,report_id,reviewer_issuer,reviewer_subject,disposition) VALUES(%s,%s,%s,%s,%s)",
                (p.tenant, rid, p.issuer, p.subject, body.disposition),
            )
            row = db.execute(
                "UPDATE feedback.report SET state=%s,expires_at=CASE WHEN %s='rejected' THEN least(expires_at,created_at+interval '7 days') ELSE expires_at END WHERE tenant=%s AND id=%s RETURNING *",
                (body.disposition, body.disposition, p.tenant, rid),
            ).fetchone()
            return self.view(row)

    def publish(self, p, rid, github):
        # Persist intent before the external write. A process/connection failure
        # leaves publishing/unknown, never an automatically retryable accepted row.
        with self.transaction(p.tenant) as db:
            self.member(db, p, reviewer=True)
            row = self.get(db, p, rid)
            if row["state"] == "published":
                return self.view(row)
            if row["state"] != "accepted" or row["expires_at"] <= datetime.now(
                timezone.utc
            ):
                raise Refusal(409, "Accepted unexpired report required")
            db.execute(
                "UPDATE feedback.report SET state='publishing',external_pending=true WHERE tenant=%s AND id=%s",
                (p.tenant, rid),
            )
        with self.transaction(p.tenant) as db:
            self.member(db, p, reviewer=True)
            row = self.get(db, p, rid)
            if row["state"] != "publishing" or row["expires_at"] <= datetime.now(
                timezone.utc
            ):
                raise Refusal(409, "Publication interrupted")
            try:
                number = github.create(row)
                if type(number) is not int or number < 1:
                    raise ValueError("Invalid issue number")
            except Exception:
                row = db.execute(
                    "UPDATE feedback.report SET state='unknown' WHERE tenant=%s AND id=%s RETURNING *",
                    (p.tenant, rid),
                ).fetchone()
            else:
                row = db.execute(
                    "UPDATE feedback.report SET state='published',issue_number=%s,external_pending=false WHERE tenant=%s AND id=%s RETURNING *",
                    (number, p.tenant, rid),
                ).fetchone()
            return self.view(row)

    def reconcile(self, p, rid, number, github):
        with self.transaction(p.tenant) as db:
            self.member(db, p, reviewer=True)
            row = self.get(db, p, rid)
            if row["state"] not in ("unknown", "publishing") or row[
                "expires_at"
            ] <= datetime.now(timezone.utc):
                raise Refusal(409, "Unresolved publication required")
            if not github.verify(row, number):
                raise Refusal(409, "Issue does not match vetted report")
            row = db.execute(
                "UPDATE feedback.report SET state='published',issue_number=%s,external_pending=false WHERE tenant=%s AND id=%s RETURNING *",
                (number, p.tenant, rid),
            ).fetchone()
            return self.view(row)

    def close(self, p, rid):
        with self.transaction(p.tenant) as db:
            self.member(db, p, reviewer=True)
            row = self.get(db, p, rid)
            if row["state"] not in ("accepted", "published"):
                raise Refusal(409, "Accepted report required")
            row = db.execute(
                "UPDATE feedback.report SET closed_at=coalesce(closed_at,now()),expires_at=least(expires_at,coalesce(closed_at,now())+interval '90 days') WHERE tenant=%s AND id=%s RETURNING *",
                (p.tenant, rid),
            ).fetchone()
            return self.view(row)

    def export(self, p):
        with self.transaction(p.tenant) as db:
            self.member(db, p, rights=True)
            args = (p.tenant, p.issuer, p.subject)
            reports = db.execute(
                "SELECT * FROM feedback.report r WHERE tenant=%s AND issuer=%s AND subject=%s AND state<>'deleted' AND expires_at>now() AND NOT EXISTS(SELECT 1 FROM feedback.erasure e WHERE e.tenant=r.tenant AND e.issuer=r.issuer AND e.subject=r.subject) ORDER BY created_at,id",
                args,
            ).fetchall()
            consent = db.execute(
                "SELECT device,revision,enabled,notice,purpose,changed_at FROM feedback.consent_history WHERE tenant=%s AND issuer=%s AND subject=%s AND (expires_at IS NULL OR expires_at>now())",
                args,
            ).fetchall()
            destinations = db.execute(
                "SELECT id,repository,issue_number,external_pending FROM feedback.report WHERE tenant=%s AND issuer=%s AND subject=%s AND (issue_number IS NOT NULL OR external_pending)",
                args,
            ).fetchall()
            return {
                "reports": [self.view(r) for r in reports],
                "consent": consent,
                "destinations": destinations,
            }

    def erase(self, p):
        with self.transaction(p.tenant) as db:
            self.member(db, p, rights=True)
            args = (p.tenant, p.issuer, p.subject)
            db.execute(
                "INSERT INTO feedback.erasure(tenant,issuer,subject) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                args,
            )
            db.execute(
                "UPDATE feedback.consent SET enabled=false,revision=revision+1,changed_at=now() WHERE tenant=%s AND issuer=%s AND subject=%s AND enabled",
                args,
            )
            db.execute(
                """UPDATE feedback.consent_history c SET expires_at=least(coalesce(c.expires_at,e.expires_at),e.expires_at)
                FROM feedback.erasure e WHERE c.tenant=%s AND c.issuer=%s AND c.subject=%s
                AND e.tenant=c.tenant AND e.issuer=c.issuer AND e.subject=c.subject""",
                args,
            )
            db.execute(
                "UPDATE feedback.report SET state='deleted',title='',body='',fingerprint='',external_pending=(external_pending OR issue_number IS NOT NULL),expires_at=least(expires_at,now()) WHERE tenant=%s AND issuer=%s AND subject=%s",
                args,
            )
            pending = db.execute(
                "SELECT id,repository,issue_number FROM feedback.report WHERE tenant=%s AND issuer=%s AND subject=%s AND external_pending",
                args,
            ).fetchall()
            return {
                "local_deleted": True,
                "complete": not pending,
                "external_pending": pending,
                "retained_metadata": "Consent and request metadata retained up to 24 months; no report text retained",
            }

    def purge(self, tenant):
        with self.transaction(tenant) as db:
            db.execute(
                """UPDATE feedback.consent c SET enabled=false,revision=revision+1,changed_at=least(c.changed_at,e.requested_at)
                FROM feedback.erasure e WHERE c.tenant=%s AND c.enabled
                AND e.tenant=c.tenant AND e.issuer=c.issuer AND e.subject=c.subject""",
                (tenant,),
            )
            db.execute(
                """UPDATE feedback.consent_history c SET expires_at=least(coalesce(c.expires_at,e.expires_at),e.expires_at)
                FROM feedback.erasure e WHERE c.tenant=%s
                AND e.tenant=c.tenant AND e.issuer=c.issuer AND e.subject=c.subject""",
                (tenant,),
            )
            db.execute(
                "UPDATE feedback.report SET state='deleted',title='',body='',fingerprint='',external_pending=(external_pending OR issue_number IS NOT NULL),expires_at=least(expires_at,now()) WHERE tenant=%s AND state<>'deleted' AND (expires_at<=now() OR EXISTS(SELECT 1 FROM feedback.erasure e WHERE e.tenant=feedback.report.tenant AND e.issuer=feedback.report.issuer AND e.subject=feedback.report.subject))",
                (tenant,),
            )
            # Keep only content-free destination pointers for unresolved processor
            # deletion. Report bodies cannot survive expiry or a request.
            db.execute(
                "DELETE FROM feedback.report WHERE tenant=%s AND state='deleted' AND NOT external_pending AND expires_at<now()-interval '30 days'",
                (tenant,),
            )
            db.execute(
                "DELETE FROM feedback.consent WHERE tenant=%s AND NOT enabled AND changed_at<now()-interval '24 months'",
                (tenant,),
            )
            db.execute(
                "DELETE FROM feedback.consent_history WHERE tenant=%s AND expires_at<=now()",
                (tenant,),
            )
            db.execute(
                """DELETE FROM feedback.erasure e WHERE tenant=%s AND expires_at<=now()
                AND NOT EXISTS(SELECT 1 FROM feedback.report r WHERE r.tenant=e.tenant AND r.issuer=e.issuer AND r.subject=e.subject AND r.external_pending)""",
                (tenant,),
            )

    def cleanup_destinations(self, tenant, github):
        with self.transaction(tenant) as db:
            ids = db.execute(
                """SELECT id FROM feedback.report WHERE tenant=%s AND state='deleted'
                AND external_pending AND issue_number IS NOT NULL AND cleanup_after<=now()
                ORDER BY cleanup_after,id LIMIT 10""",
                (tenant,),
            ).fetchall()
        for item in ids:
            # Each attempt commits independently. Failed destinations move to
            # a later retry slot instead of starving every later ID.
            with self.transaction(tenant) as db:
                row = db.execute(
                    """SELECT * FROM feedback.report WHERE tenant=%s AND id=%s
                    AND state='deleted' AND external_pending AND issue_number IS NOT NULL
                    AND cleanup_after<=now() FOR UPDATE""",
                    (tenant, item["id"]),
                ).fetchone()
                if row is None:
                    continue
                db.execute(
                    "UPDATE feedback.report SET cleanup_after=now()+interval '5 minutes' WHERE tenant=%s AND id=%s",
                    (tenant, row["id"]),
                )
                try:
                    confirmed = github.delete(row)
                except Exception:
                    continue
                if confirmed is True:
                    db.execute(
                        "UPDATE feedback.report SET external_pending=false,issue_number=NULL WHERE tenant=%s AND id=%s",
                        (tenant, row["id"]),
                    )
