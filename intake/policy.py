"""Bounded input and deterministic minimization; free text still needs review."""

import hashlib
import json
import re
import unicodedata
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

NOTICE = "human-reports-v1"
PURPOSE = "product-feedback"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Consent(Input):
    enabled: bool
    expected_revision: int = Field(ge=0)
    notice: Literal["human-reports-v1"]
    purpose: Literal["product-feedback"]


class Submission(Input):
    consent_revision: int = Field(ge=1)
    category: Literal["bug", "feature"]
    component: str = Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")
    title: str = Field(min_length=4, max_length=160)
    body: str = Field(min_length=10, max_length=4000)
    preview_confirmed: Literal[True]


class Decision(Input):
    disposition: Literal["accepted", "rejected"]
    human_reviewed: Literal[True]


class Reconcile(Input):
    issue_number: int = Field(ge=1)


# Never persist or echo the original payload. Known excerpt containers are
# removed as a whole, not merely their labels. No automatic attachments exist.
EXCERPT = re.compile(
    r"(?is)\b(?:prompt(?: body)?|file contents?|clipboard|conversation|source code)\s*:.*\Z"
)
# Match assignment labels once, without repartitioning namespace components.
# Possessive label scanning prevents backtracking through long pasted names.
ASSIGNMENT = re.compile(r"""(?<![\w-])["']?([A-Za-z][A-Za-z0-9_ -]*+)["']?\s*[:=]""")
SECRET_NAMES = (
    "password",
    "passphrase",
    "passwd",
    "apikey",
    "token",
    "secret",
    "credential",
    "privatekey",
    "authorization",
    "cookie",
    "serial",
    "prompt",
    "filecontents",
    "clipboard",
    "conversation",
    "sourcecode",
)


def scrub_assignments(text):
    for match in ASSIGNMENT.finditer(text):
        label = re.sub(r"[_ -]", "", match[1]).casefold()
        if label == "file" or any(name in label for name in SECRET_NAMES):
            # Discard the whole remaining excerpt, including multiline values.
            return text[: match.start()] + "[secret removed]"
    return text


REDACTIONS = [
    (r"(?s)```.*?(?:```|\Z)", "[excerpt removed]"),
    (r"(?is)-----BEGIN [^-]*PRIVATE KEY-----.*", "[secret removed]"),
    (r"(?i)\bbearer\s+\S+", "[secret removed]"),
    (
        r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]+)\b",
        "[secret removed]",
    ),
    (r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[secret removed]"),
    (r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[email removed]"),
    (r"(?i)\b(?:https?://|www\.)\S+", "[url removed]"),
    (
        r"\b(?:\d{1,3}\.){3}\d{1,3}\b|\b(?:[a-fA-F0-9]{1,4}:){2,}[a-fA-F0-9:]+\b",
        "[address removed]",
    ),
    (r"(?i)(?:[A-Z]:\\|/(?:home|Users|var|tmp)/)[^\s]+", "[path removed]"),
    (r"(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)", "[number removed]"),
    (r"(?<!\w)@[\w-]+", "[mention removed]"),
]


def scrub(value):
    text = unicodedata.normalize("NFKC", value)
    text = "".join(c for c in text if c in "\n\t" or unicodedata.category(c)[0] != "C")
    # Logs often contain JSON quoted inside JSON. Remove quote escaping before
    # matching labels; repeated scrub calls must not reveal another layer.
    text = re.sub(r"\\+(?=[\"'])", "", text)
    text = EXCERPT.sub("\n[excerpt removed]", text)
    for pattern, replacement in REDACTIONS:
        text = re.sub(pattern, replacement, text)
    text = scrub_assignments(text)
    # HTML and markdown embeds are not needed in the plain-text intake schema.
    text = text.replace("<", "‹").replace(">", "›")
    return text.strip()


def prepare(body):
    raw = body.model_dump()
    links = len(re.findall(r"(?i)https?://|www\.", raw["body"]))
    title, text = scrub(raw["title"])[:160], scrub(raw["body"])[:4000]
    spam = (
        links > 3
        or len(set(text.casefold())) < 5
        or bool(re.search(r"(.)\1{30}", text))
    )
    if len(title) < 4 or len(text) < 10:
        spam = True
    digest = hashlib.sha256(
        json.dumps(
            [
                raw["category"],
                raw["component"],
                " ".join(title.casefold().split()),
                " ".join(text.casefold().split()),
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return title, text, digest, spam


def payload(row):
    # Repeat minimization at the external boundary. Identifying scope is never
    # included in the GitHub payload, even in hidden markup.
    return {
        "title": scrub(row["title"]),
        "body": scrub(row["body"]) + f"\n\n<!-- spanopticon-intake:{row['id']} -->",
    }
