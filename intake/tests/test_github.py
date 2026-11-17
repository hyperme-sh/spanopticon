"""External HTTP contracts use a fake transport; no GitHub writes occur."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import httpx
from intake.github import GitHub
from intake.policy import payload


class GitHubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "token"
        self.path.write_text("synthetic-installation-token")
        self.api = GitHub(self.path, {"example/app"})
        self.row = {
            "id": uuid4(),
            "repository": "example/app",
            "title": "Report title",
            "body": "Contact alice@example.com",
            "issue_number": 42,
        }

    def transport(self, handler):
        original = httpx.Client

        def factory(**kwargs):
            self.assertFalse(kwargs["follow_redirects"])
            self.assertFalse(kwargs["trust_env"])
            return original(transport=httpx.MockTransport(handler), **kwargs)

        return patch("intake.github.httpx.Client", side_effect=factory)

    def test_create_is_scrubbed_and_fixed_origin(self):
        def handler(request):
            self.assertEqual(
                str(request.url), "https://api.github.com/repos/example/app/issues"
            )
            self.assertEqual(request.method, "POST")
            self.assertNotIn(b"alice", request.content)
            self.assertNotIn(b"tenant", request.content)
            return httpx.Response(201, json={"number": 42})

        with self.transport(handler):
            self.assertEqual(self.api.create(self.row), 42)

    def test_no_redirect_or_unconfigured_repository(self):
        with self.transport(
            lambda request: httpx.Response(
                302, headers={"Location": "https://attacker.test"}
            )
        ):
            with self.assertRaises(ValueError):
                self.api.create(self.row)
        with self.assertRaises(ValueError):
            self.api.create(self.row | {"repository": "other/app"})

    def test_reconcile_requires_exact_marker_and_content(self):
        with self.transport(
            lambda request: httpx.Response(200, json={"number": 42} | payload(self.row))
        ):
            self.assertTrue(self.api.verify(self.row, 42))
        with self.transport(
            lambda request: httpx.Response(
                200, json={"number": 42, "title": "other", "body": "other"}
            )
        ):
            self.assertFalse(self.api.verify(self.row, 42))

    def test_delete_only_matching_issue_and_explicit_mutation_success(self):
        def handler(request):
            if request.method == "GET":
                return httpx.Response(
                    200, json={"number": 42, "node_id": "test-node"} | payload(self.row)
                )
            self.assertEqual(str(request.url), "https://api.github.com/graphql")
            return httpx.Response(
                200,
                json={
                    "data": {"deleteIssue": {"clientMutationId": str(self.row["id"])}}
                },
            )

        with self.transport(handler):
            self.assertTrue(self.api.delete(self.row))
        with self.transport(
            lambda request: httpx.Response(
                200, json={"number": 42, "node_id": "test-node", "body": "unrelated"}
            )
        ):
            with self.assertRaises(ValueError):
                self.api.delete(self.row)

    def test_failed_deletion_is_not_success(self):
        def handler(request):
            if request.method == "GET":
                return httpx.Response(
                    200, json={"number": 42, "node_id": "test-node"} | payload(self.row)
                )
            return httpx.Response(200, json={"errors": [{"message": "forbidden"}]})

        with self.transport(handler):
            with self.assertRaises(ValueError):
                self.api.delete(self.row)
