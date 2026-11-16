"""CI-only PostgreSQL contracts; GitHub is an explicit deterministic double."""

from concurrent.futures import ThreadPoolExecutor
import os
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from intake.admin import migrate, members
from intake.store import Store, Principal, Refusal
from intake.policy import Consent, Decision
from intake.tests.test_policy import submission


@unittest.skipUnless(
    os.environ.get("INTAKE_CONTRACT_ADMIN_DSN"), "PostgreSQL contracts run only in CI"
)
class PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = os.environ["INTAKE_CONTRACT_ADMIN_DSN"]
        with psycopg.connect(cls.admin, row_factory=dict_row) as db:
            migrate(db)
            db.execute(
                "CREATE ROLE intake_contract LOGIN PASSWORD 'intake-contract-only' NOSUPERUSER NOBYPASSRLS"
            )
            db.execute("GRANT USAGE ON SCHEMA feedback TO intake_contract")
            db.execute(
                "GRANT SELECT ON feedback.schema_version,feedback.member TO intake_contract"
            )
            db.execute(
                "GRANT SELECT,INSERT,UPDATE,DELETE ON feedback.consent,feedback.consent_history,feedback.report,feedback.decision,feedback.erasure TO intake_contract"
            )
        cls.store = Store(os.environ["INTAKE_CONTRACT_DSN"])
        cls.store.ready()

    def setUp(self):
        self.tenant = str(uuid4())
        self.other_tenant = str(uuid4())
        self.owner = Principal(self.tenant, "https://issuer.test", "sender", "device-a")
        self.reviewer = Principal(
            self.tenant, "https://issuer.test", "reviewer", "review-device"
        )
        self.peer = Principal(
            self.tenant, "https://issuer.test", "other-sender", "device-b"
        )
        self.foreign = Principal(
            self.other_tenant, "https://issuer.test", "reviewer", "review-device"
        )
        with psycopg.connect(self.admin, row_factory=dict_row) as db:
            members(
                db,
                [
                    {
                        "tenant": p.tenant,
                        "issuer": p.issuer,
                        "subject": p.subject,
                        "role": role,
                        "enabled": True,
                    }
                    for p, role in (
                        (self.owner, "submitter"),
                        (self.peer, "submitter"),
                        (self.reviewer, "reviewer"),
                        (self.foreign, "reviewer"),
                    )
                ],
            )
        self.optin(self.owner)
        self.optin(self.peer)

    def optin(self, p):
        return self.store.consent(
            p,
            Consent(
                enabled=True,
                expected_revision=0,
                notice="human-reports-v1",
                purpose="product-feedback",
            ),
        )

    def submit(self, p=None, **fields):
        return self.store.submit(
            p or self.owner, uuid4(), submission(**fields), "example/app"
        )

    def accept(self, row):
        return self.store.decide(
            self.reviewer,
            row["id"],
            Decision(disposition="accepted", human_reviewed=True),
        )

    def test_consent_default_off_stale_revision_and_withdrawal(self):
        absent = Principal(
            self.tenant, self.owner.issuer, self.owner.subject, "other-device"
        )
        self.assertFalse(self.store.consent(absent)["enabled"])
        with self.assertRaises(Refusal):
            self.submit(absent)
        with self.assertRaises(Refusal):
            self.submit(consent_revision=2)
        self.store.consent(
            self.owner,
            Consent(
                enabled=False,
                expected_revision=1,
                notice="human-reports-v1",
                purpose="product-feedback",
            ),
        )
        with self.assertRaises(Refusal):
            self.submit()
        with self.assertRaises(Refusal):
            self.optin(self.owner)
        self.assertEqual(len(self.store.export(self.owner)["consent"]), 2)

    def test_idempotency_and_duplicate_disposition(self):
        key = uuid4()
        body = submission()
        first = self.store.submit(self.owner, key, body, "example/app")
        self.assertEqual(
            self.store.submit(self.owner, key, body, "example/app")["id"], first["id"]
        )
        with self.assertRaises(Refusal):
            self.store.submit(
                self.owner, key, submission(title="Other title"), "example/app"
            )
        self.assertEqual(self.submit(self.peer)["state"], "duplicate")
        self.assertEqual(len(self.store.reports(self.owner)), 1)

    def test_concurrent_dedup_and_budget(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda p: self.submit(p), (self.owner, self.peer)))
        self.assertEqual(sorted(r["state"] for r in results), ["duplicate", "pending"])
        for index in range(9):
            self.submit(title=f"Another report {index}")
        with self.assertRaises(Refusal) as raised:
            self.submit(title="Limit exceeded")
        self.assertEqual(raised.exception.status, 429)
        self.assertIn("reports", self.store.export(self.owner))

    def test_rls_rejects_unscoped_and_cross_tenant_sql(self):
        row = self.submit()
        with psycopg.connect(self.store.dsn) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM feedback.report").fetchone()[0], 0
            )
        with self.store.transaction(self.other_tenant) as db:
            self.assertIsNone(
                db.execute(
                    "SELECT * FROM feedback.report WHERE id=%s", (row["id"],)
                ).fetchone()
            )
            self.assertEqual(
                db.execute(
                    "UPDATE feedback.report SET title='tampered' WHERE id=%s",
                    (row["id"],),
                ).rowcount,
                0,
            )
        with self.assertRaises(Refusal):
            self.store.decide(
                self.foreign,
                row["id"],
                Decision(disposition="accepted", human_reviewed=True),
            )
        self.assertEqual(self.store.reports(self.foreign, review=True), [])

    def test_submitter_cannot_review_and_spam_cannot_publish(self):
        row = self.submit()
        with self.assertRaises(Refusal):
            self.store.decide(
                self.owner,
                row["id"],
                Decision(disposition="accepted", human_reviewed=True),
            )
        spam = self.submit(body="https://spam.test " * 6)
        self.assertEqual(spam["state"], "spam")
        with self.assertRaises(Refusal):
            self.accept(spam)
        github = Mock()
        with self.assertRaises(Refusal):
            self.store.publish(self.reviewer, spam["id"], github)
        github.create.assert_not_called()

    def test_vetted_publish_and_ambiguous_write_never_retries(self):
        row = self.submit()
        self.accept(row)
        github = Mock()
        github.create.return_value = 12
        result = self.store.publish(self.reviewer, row["id"], github)
        self.assertEqual(result["issue_number"], 12)
        self.assertEqual(
            self.store.publish(self.reviewer, row["id"], github)["state"], "published"
        )
        self.assertEqual(github.create.call_count, 1)
        other = self.submit(title="Different report")
        self.accept(other)
        github.create.side_effect = TimeoutError("private provider details")
        self.assertEqual(
            self.store.publish(self.reviewer, other["id"], github)["state"], "unknown"
        )
        with self.assertRaises(Refusal):
            self.store.publish(self.reviewer, other["id"], github)
        self.assertEqual(github.create.call_count, 2)
        github.verify.return_value = False
        with self.assertRaises(Refusal):
            self.store.reconcile(self.reviewer, other["id"], 13, github)
        github.verify.return_value = True
        self.assertEqual(
            self.store.reconcile(self.reviewer, other["id"], 13, github)["state"],
            "published",
        )

    def test_erasure_is_subject_scoped_and_external_failures_are_explicit(self):
        row = self.submit()
        self.accept(row)
        github = Mock()
        github.create.return_value = 22
        self.store.publish(self.reviewer, row["id"], github)
        self.submit(self.peer, title="Other person report")
        result = self.store.erase(self.owner)
        self.assertTrue(result["local_deleted"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["external_pending"][0]["issue_number"], 22)
        self.assertEqual(self.store.export(self.owner)["reports"], [])
        self.assertEqual(len(self.store.reports(self.peer)), 1)
        with self.assertRaises(Refusal):
            self.submit()
        self.assertFalse(self.store.consent(self.owner)["enabled"])
        with self.assertRaises(Refusal):
            self.store.consent(
                self.owner,
                Consent(
                    enabled=True,
                    expected_revision=2,
                    notice="human-reports-v1",
                    purpose="product-feedback",
                ),
            )
        with self.store.transaction(self.tenant) as db:
            stored = db.execute(
                "SELECT body,title,fingerprint FROM feedback.report WHERE tenant=%s AND id=%s",
                (self.tenant, row["id"]),
            ).fetchone()
            self.assertEqual(stored, {"body": "", "title": "", "fingerprint": ""})

    def test_retention_hides_expired_rows_before_sweep_and_clears_text(self):
        row = self.submit()
        with self.store.transaction(self.tenant) as db:
            db.execute(
                "UPDATE feedback.report SET expires_at=now()-interval '1 second' WHERE tenant=%s AND id=%s",
                (self.tenant, row["id"]),
            )
        self.assertEqual(self.store.reports(self.owner), [])
        with self.assertRaises(Refusal):
            self.accept(row)
        self.store.purge(self.tenant)
        with self.store.transaction(self.tenant) as db:
            stored = db.execute(
                "SELECT state,body FROM feedback.report WHERE tenant=%s AND id=%s",
                (self.tenant, row["id"]),
            ).fetchone()
            self.assertEqual(stored, {"state": "deleted", "body": ""})

    def test_rejection_and_closure_only_shorten_retention(self):
        row = self.submit()
        rejected = self.store.decide(
            self.reviewer,
            row["id"],
            Decision(disposition="rejected", human_reviewed=True),
        )
        self.assertEqual((rejected["expires_at"] - rejected["created_at"]).days, 7)
        accepted = self.submit(title="Accepted report")
        self.accept(accepted)
        closed = self.store.close(self.reviewer, accepted["id"])
        self.assertEqual((closed["expires_at"] - closed["closed_at"]).days, 90)
        self.assertEqual(
            self.store.close(self.reviewer, accepted["id"])["expires_at"],
            closed["expires_at"],
        )

    def test_runtime_rejects_bypass_role(self):
        with self.assertRaises(RuntimeError):
            Store(self.admin).ready()

    def test_processor_deletion_retries_and_reports_completion(self):
        row = self.submit()
        self.accept(row)
        github = Mock()
        github.create.return_value = 42
        self.store.publish(self.reviewer, row["id"], github)
        self.assertFalse(self.store.erase(self.owner)["complete"])
        github.delete.side_effect = TimeoutError()
        self.store.cleanup_destinations(self.tenant, github)
        self.assertFalse(self.store.erase(self.owner)["complete"])
        with self.store.transaction(self.tenant) as db:
            db.execute(
                "UPDATE feedback.report SET cleanup_after=now() WHERE tenant=%s",
                (self.tenant,),
            )
        github.delete.side_effect = None
        github.delete.return_value = True
        self.store.cleanup_destinations(self.tenant, github)
        self.assertTrue(self.store.erase(self.owner)["complete"])

    def test_retained_erasure_blocks_restored_text_before_sweep(self):
        row = self.submit()
        self.store.erase(self.owner)
        with self.store.transaction(self.tenant) as db:
            db.execute(
                "UPDATE feedback.report SET state='pending',title='Restored title',body='restored private text',expires_at=now()+interval '180 days' WHERE tenant=%s AND id=%s",
                (self.tenant, row["id"]),
            )
            db.execute(
                "UPDATE feedback.consent SET enabled=true WHERE tenant=%s",
                (self.tenant,),
            )
        self.assertEqual(self.store.reports(self.owner), [])
        self.assertEqual(self.store.reports(self.reviewer, review=True), [])
        self.assertEqual(self.store.export(self.owner)["reports"], [])
        with self.assertRaises(Refusal):
            self.accept(row)
        self.store.purge(self.tenant)
        self.assertFalse(self.store.consent(self.owner)["enabled"])
        with self.store.transaction(self.tenant) as db:
            self.assertEqual(
                db.execute(
                    "SELECT body FROM feedback.report WHERE tenant=%s AND id=%s",
                    (self.tenant, row["id"]),
                ).fetchone()["body"],
                "",
            )

    def test_review_cursor_is_scoped(self):
        first = self.submit(title="First distinct report")
        second = self.submit(title="Second distinct report")
        ordered = sorted([first["id"], second["id"]])
        self.assertEqual(
            [
                r["id"]
                for r in self.store.reports(
                    self.reviewer, review=True, after=ordered[0]
                )
            ],
            [ordered[1]],
        )
        self.assertEqual(
            self.store.reports(self.foreign, review=True, after=ordered[0]), []
        )

    def test_failed_destinations_do_not_starve_later_requests(self):
        from uuid import UUID

        with self.store.transaction(self.tenant) as db:
            for number in range(1, 12):
                db.execute(
                    """INSERT INTO feedback.report(tenant,id,issuer,subject,device,request_id,
                    consent_revision,category,component,repository,title,body,fingerprint,state,expires_at,issue_number,external_pending)
                    VALUES(%s,%s,%s,%s,%s,%s,1,'bug','web','example/app','','','','deleted',now(),%s,true)""",
                    (
                        self.tenant,
                        UUID(int=number),
                        self.owner.issuer,
                        self.owner.subject,
                        self.owner.device,
                        uuid4(),
                        number,
                    ),
                )
        attempted = []

        def remove(row):
            attempted.append(row["issue_number"])
            if row["issue_number"] <= 10:
                raise ValueError("Changed marker")
            return True

        github = Mock()
        github.delete.side_effect = remove
        self.store.cleanup_destinations(self.tenant, github)
        self.store.cleanup_destinations(self.tenant, github)
        self.assertEqual(attempted, list(range(1, 12)))
        with self.store.transaction(self.tenant) as db:
            self.assertFalse(
                db.execute(
                    "SELECT external_pending FROM feedback.report WHERE tenant=%s AND issue_number IS NULL",
                    (self.tenant,),
                ).fetchone()["external_pending"]
            )

    def test_restored_consent_history_keeps_original_erasure_deadline(self):
        self.store.erase(self.owner)
        with self.store.transaction(self.tenant) as db:
            db.execute(
                "UPDATE feedback.erasure SET requested_at=now()-interval '23 months',expires_at=now()+interval '1 month' WHERE tenant=%s",
                (self.tenant,),
            )
            original = db.execute(
                "SELECT expires_at FROM feedback.erasure WHERE tenant=%s",
                (self.tenant,),
            ).fetchone()["expires_at"]
            db.execute(
                "UPDATE feedback.consent_history SET expires_at=NULL WHERE tenant=%s AND subject=%s",
                (self.tenant, self.owner.subject),
            )
        self.store.purge(self.tenant)
        self.store.erase(self.owner)
        self.store.purge(self.tenant)
        with self.store.transaction(self.tenant) as db:
            rows = db.execute(
                "SELECT expires_at FROM feedback.consent_history WHERE tenant=%s AND subject=%s",
                (self.tenant, self.owner.subject),
            ).fetchall()
        self.assertTrue(rows)
        self.assertTrue(all(row["expires_at"] == original for row in rows))

    def test_consent_noops_and_bounded_history_preserve_withdrawal(self):
        def change(p, enabled, revision):
            return self.store.consent(
                p,
                Consent(
                    enabled=enabled,
                    expected_revision=revision,
                    notice="human-reports-v1",
                    purpose="product-feedback",
                ),
            )

        for _ in range(5):
            self.assertEqual(change(self.owner, True, 1)["revision"], 1)
        self.assertEqual(len(self.store.export(self.owner)["consent"]), 1)
        absent = Principal(
            self.tenant, self.owner.issuer, self.owner.subject, "unused-device"
        )
        self.assertEqual(change(absent, False, 0)["revision"], 0)
        self.assertEqual(len(self.store.export(self.owner)["consent"]), 1)
        for field, limit in (
            ("CONSENT_SUBJECT_LIMIT", 2),
            ("CONSENT_TENANT_LIMIT", 4),
            ("CONSENT_SUBJECT_HOURLY", 1),
            ("CONSENT_TENANT_HOURLY", 2),
        ):
            with self.subTest(field=field), patch.object(self.store, field, limit):
                with self.assertRaises(Refusal) as raised:
                    change(absent, True, 0)
                self.assertEqual(raised.exception.status, 429)
        with (
            patch.object(self.store, "CONSENT_SUBJECT_LIMIT", 2),
            patch.object(self.store, "CONSENT_SUBJECT_HOURLY", 1),
        ):
            self.assertEqual(change(self.owner, False, 1)["revision"], 2)
            self.assertEqual(change(self.owner, False, 2)["revision"], 2)
            with self.assertRaises(Refusal):
                change(self.owner, True, 2)
        self.assertEqual(len(self.store.export(self.owner)["consent"]), 2)

    def test_reviewer_cannot_consent_or_submit_and_submitter_cannot_review(self):
        for operation in (
            lambda: self.optin(self.reviewer),
            lambda: self.submit(self.reviewer),
        ):
            with self.assertRaises(Refusal) as raised:
                operation()
            self.assertEqual(raised.exception.status, 403)
        row = self.submit()
        for operation in (
            lambda: self.store.reports(self.owner, review=True),
            lambda: self.store.decide(
                self.owner,
                row["id"],
                Decision(disposition="accepted", human_reviewed=True),
            ),
            lambda: self.store.publish(self.owner, row["id"], Mock()),
        ):
            with self.assertRaises(Refusal) as raised:
                operation()
            self.assertEqual(raised.exception.status, 403)
