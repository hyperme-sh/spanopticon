"""Fixed-origin GitHub App installation-token adapter, without implicit retries."""

from pathlib import Path
import re
import httpx
from .policy import payload


class GitHub:
    def __init__(self, token_file, repositories):
        self.token_file = token_file
        self.repositories = repositories

    def request(self, method, repository, suffix="", body=None):
        if repository not in self.repositories or not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository
        ):
            raise ValueError("Invalid destination")
        token = Path(self.token_file).read_text().strip()
        if not token or "\n" in token:
            raise ValueError("Missing machine credential")
        with httpx.Client(
            timeout=10, follow_redirects=False, trust_env=False
        ) as client:
            response = client.request(
                method,
                f"https://api.github.com/repos/{repository}/issues{suffix}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json=body,
            )
            if response.status_code not in (200, 201):
                raise ValueError("GitHub operation not confirmed")
            value = response.json()
            if (
                not isinstance(value, dict)
                or type(value.get("number")) is not int
                or value["number"] < 1
                or "pull_request" in value
            ):
                raise ValueError("Invalid issue response")
            return value

    def create(self, row):
        return self.request("POST", row["repository"], body=payload(row))["number"]

    def verify(self, row, number):
        value = self.request("GET", row["repository"], f"/{number}")
        expected = payload(row)
        return (
            value["number"] == number
            and value.get("title") == expected["title"]
            and value.get("body") == expected["body"]
        )

    def delete(self, row):
        value = self.request("GET", row["repository"], f"/{row['issue_number']}")
        marker = f"<!-- spanopticon-intake:{row['id']} -->"
        if marker not in (value.get("body") or "") or not isinstance(
            value.get("node_id"), str
        ):
            raise ValueError("Destination no longer matches intake")
        token = Path(self.token_file).read_text().strip()
        with httpx.Client(
            timeout=10, follow_redirects=False, trust_env=False
        ) as client:
            result = client.post(
                "https://api.github.com/graphql",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "query": "mutation($id:ID!,$key:String!){deleteIssue(input:{issueId:$id,clientMutationId:$key}){clientMutationId}}",
                    "variables": {"id": value["node_id"], "key": str(row["id"])},
                },
            )
            if result.status_code != 200:
                raise ValueError("Destination deletion not confirmed")
            body = result.json()
            if body.get("errors") or body.get("data", {}).get("deleteIssue", {}).get(
                "clientMutationId"
            ) != str(row["id"]):
                raise ValueError("Destination deletion not confirmed")
        return True
