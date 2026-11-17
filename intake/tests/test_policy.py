import unittest
from unittest.mock import patch
from pydantic import ValidationError
from intake import policy
from intake.__main__ import bind_address


def submission(**overrides):
    return policy.Submission(
        **(
            dict(
                consent_revision=1,
                category="bug",
                component="web",
                title="Button not working",
                body="The save button remains disabled after selecting an option.",
                preview_confirmed=True,
            )
            | overrides
        )
    )


class PolicyTests(unittest.TestCase):
    def test_allowlist_rejects_attachments_and_prompt_fields(self):
        for key in (
            "prompt",
            "file",
            "attachment",
            "tenant_id",
            "repository",
            "contact",
            "github_token",
        ):
            with self.subTest(key=key), self.assertRaises(ValidationError):
                submission(**{key: "private"})

    def test_consent_preview_and_limits(self):
        for fields in (
            {"preview_confirmed": False},
            {"consent_revision": 0},
            {"body": "x" * 4001},
            {"component": "../repo"},
        ):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                submission(**fields)

    def test_canaries_are_removed_before_storage(self):
        original = "Contact alice@example.com token=secrettoken 192.168.1.22 +1 (212) 555-1212 /home/alice/key https://example.com/?secret=abc @someone\n```file-private-content```\nprompt: prompt-private-content"
        clean = policy.scrub(original)
        for secret in (
            "alice",
            "secrettoken",
            "192.168",
            "555-1212",
            "?secret",
            "@someone",
            "file-private-content",
            "prompt-private-content",
        ):
            self.assertNotIn(secret, clean)

    def test_unclosed_code_block_is_removed(self):
        self.assertNotIn("private", policy.scrub("description\n```private"))

    def test_scrubbing_is_stable_at_external_boundary(self):
        value = policy.scrub("Email alice@example.com and password=secret")
        self.assertEqual(value, policy.scrub(value))

    def test_tenant_independent_hash_does_not_store_raw_pii(self):
        a = policy.prepare(submission(body="The button failed for alice@example.com"))
        b = policy.prepare(submission(body="The button failed for bob@example.com"))
        self.assertEqual(a[2], b[2])
        self.assertNotIn("alice", a[1])

    def test_spam_is_dispositioned_before_review(self):
        self.assertTrue(
            policy.prepare(submission(body="visit " + "https://spam.test " * 5))[3]
        )
        self.assertTrue(policy.prepare(submission(body="a" * 80))[3])
        self.assertFalse(policy.prepare(submission())[3])

    def test_private_binding(self):
        self.assertEqual(bind_address("127.0.0.1"), "127.0.0.1")
        for address in ("0.0.0.0", "::", "192.168.1.1"):
            with self.assertRaises(ValueError):
                bind_address(address)
        with patch(
            "intake.__main__.subprocess.check_output",
            return_value=b'[{"linkinfo":{"info_kind":"wireguard"},"addr_info":[{"local":"10.2.0.1"}]}]',
        ):
            self.assertEqual(bind_address("10.2.0.1", "wg0"), "10.2.0.1")
            with self.assertRaises(ValueError):
                bind_address("10.2.0.2", "wg0")
        with patch(
            "intake.__main__.subprocess.check_output",
            return_value=b'[{"linkinfo":{"info_kind":"ether"},"addr_info":[{"local":"10.2.0.1"}]}]',
        ):
            with self.assertRaises(ValueError):
                bind_address("10.2.0.1", "eth0")

    def test_authorization_schemes_and_cookie_lists_are_removed(self):
        for header in (
            "Authorization: Bearer opaque-sensitive-access-value",
            "Authorization: Basic YWxpY2U6cHJpdmF0ZS1wYXNzd29yZA==",
            'Proxy-Authorization: Digest username="alice", response="secret"',
            '"Authorization": "Bearer opaque-sensitive-access-value"',
            "Cookie: first=secret; second=another-secret",
        ):
            with self.subTest(header=header):
                clean = policy.scrub(
                    "Report description\n" + header + "\nContinue description"
                )
                self.assertEqual(clean, "Report description\n[secret removed]")
                self.assertEqual(policy.scrub(clean), clean)

    def test_inline_prompt_and_file_labels_remove_the_excerpt(self):
        for label in ("prompt:", "prompt body:", "file contents:"):
            self.assertNotIn(
                "private-canary",
                policy.scrub("The failure involved " + label + " private-canary"),
            )

    def test_quoted_credentials_and_excerpts_never_reach_stored_or_forwarded_text(self):
        import json
        from uuid import uuid4

        for key in (
            "password",
            "api_key",
            "token",
            "access_token",
            "refresh_token",
            "client_secret",
            "private_key",
            "secret",
            "prompt",
            "file_contents",
            "accessToken",
            "refreshToken",
            "clientSecret",
            "privateKey",
            "secretKey",
            "apiKey",
            "access-token",
            "client secret",
            "DATABASE_PASSWORD",
            "AWS_SECRET_ACCESS_KEY",
            "secretAccessKey",
            "sessionToken",
            "AWS_SESSION_TOKEN",
            "AZURE_CLIENT_SECRET",
            "OPENAI_API_KEY",
            "SERVICE_AUTH_TOKEN",
            "DATABASE-PASSWORD",
            "service_clientSecret",
        ):
            for value in (
                "opaque-sensitive-value",
                "multiword sensitive value",
                'escaped " sensitive value',
            ):
                with self.subTest(key=key, value=value):
                    raw = "Failure details " + json.dumps({key: value})
                    title, text, digest, spam = policy.prepare(submission(body=raw))
                    self.assertNotIn("sensitive", text)
                    self.assertEqual(policy.scrub(text), text)
                    self.assertNotIn(
                        "sensitive",
                        policy.payload({"id": uuid4(), "title": title, "body": raw})[
                            "body"
                        ],
                    )
        self.assertNotIn(
            "sensitive", policy.scrub("password='multiword sensitive value'")
        )

    def test_unterminated_or_unquoted_multiword_credentials_are_removed(self):
        for raw in (
            'password="multiword sensitive value',
            "privateKey='multiword sensitive value",
            "clientSecret=multiword sensitive value",
        ):
            clean = policy.scrub(raw)
            self.assertNotIn("sensitive", clean)
            self.assertEqual(policy.scrub(clean), clean)

    def test_multiline_credentials_are_removed_at_both_boundaries(self):
        from uuid import uuid4

        for key in (
            "clientSecret",
            "privateKey",
            "password",
            "Authorization",
            "Cookie",
        ):
            for marker in ("|", "|-", "|+", ">", ">-", ">+", '"', "'"):
                with self.subTest(key=key, marker=marker):
                    raw = f"Failure description\n{key}: {marker}\n  sensitive-canary\n  second-canary"
                    title, text, _, _ = policy.prepare(submission(body=raw))
                    for value in (
                        text,
                        policy.payload({"id": uuid4(), "title": title, "body": raw})[
                            "body"
                        ],
                    ):
                        self.assertNotIn("canary", value)
                    self.assertEqual(policy.scrub(text), text)

    def test_escaped_compound_secret_labels_are_removed(self):
        import json
        from uuid import uuid4

        for key in ("password", "AWS_SECRET_ACCESS_KEY", "SERVICE_AUTH_TOKEN"):
            raw = json.dumps({key: "sensitive-canary"})
            for depth in range(4):
                with self.subTest(key=key, depth=depth):
                    title, text, _, _ = policy.prepare(submission(body=raw))
                    self.assertNotIn("canary", text)
                    self.assertNotIn(
                        "canary",
                        policy.payload({"id": uuid4(), "title": title, "body": raw})[
                            "body"
                        ],
                    )
                    self.assertEqual(policy.scrub(text), text)
                raw = json.dumps(raw)

    def test_assignment_scanning_has_bounded_work_on_long_nonassignments(self):
        import time

        started = time.monotonic()
        for value in ("secret_" * 570, "secret " * 570, "x" * 4000):
            self.assertEqual(policy.scrub(value), value.strip())
            self.assertNotIn("canary", policy.scrub(value + " PASSWORD=canary"))
        self.assertLess(time.monotonic() - started, 2)

    def test_normalized_storage_lengths_remain_bounded(self):
        title, body, _, _ = policy.prepare(
            submission(title="\ufdfa" * 160, body="\ufdfa" * 4000)
        )
        self.assertLessEqual(len(title), 160)
        self.assertLessEqual(len(body), 4000)

    def test_connection_strings_and_uri_credentials_are_removed(self):
        from uuid import uuid4

        for raw in (
            "DATABASE_URL=postgresql://alice:sensitive-canary@localhost/app",
            "Details redis://alice:sensitive-canary@localhost/0",
            "Details mongodb+srv://alice:sensitive-canary@localhost/app",
            "Details amqps://alice:sensitive-canary@localhost/app",
            "ConnectionString=Server=localhost;User Id=alice;Pwd=sensitive-canary",
            "DSN=host=localhost user=alice password=sensitive-canary",
            "Server=localhost;Pwd=sensitive-canary",
            "AccountKey=sensitive-canary",
        ):
            with self.subTest(raw=raw):
                title, text, _, _ = policy.prepare(submission(body=raw))
                self.assertNotIn("canary", text)
                self.assertNotIn(
                    "canary",
                    policy.payload({"id": uuid4(), "title": title, "body": raw})[
                        "body"
                    ],
                )

    def test_cli_secret_flags_are_removed_at_both_boundaries(self):
        from uuid import uuid4

        for flag in (
            "--password",
            "--secret",
            "--client-secret",
            "--api-key",
            "--token",
            "-p",
            "-P",
            "--user",
        ):
            for separator in (" ", "\t", "\n", "="):
                raw = f"Command failed: login {flag}{separator}'sensitive-canary' --other flag"
                with self.subTest(flag=flag, separator=separator):
                    title, text, _, _ = policy.prepare(submission(body=raw))
                    self.assertNotIn("canary", text)
                    self.assertNotIn(
                        "canary",
                        policy.payload({"id": uuid4(), "title": title, "body": raw})[
                            "body"
                        ],
                    )

    def test_arbitrary_cli_aliases_and_joined_values_are_removed(self):
        from uuid import uuid4

        for raw in (
            "redis-cli -a sensitive-canary",
            "redis-cli -asensitive-canary",
            "client -Z sensitive-canary",
            "tool --custom-auth sensitive-canary",
        ):
            title, text, _, _ = policy.prepare(submission(body=raw))
            self.assertNotIn("canary", text)
            self.assertNotIn(
                "canary",
                policy.payload({"id": uuid4(), "title": title, "body": raw})["body"],
            )
