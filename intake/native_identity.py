"""Dedicated Plane grants: fixed audience, short lifetime, submitter routes only."""
from uuid import UUID
import jwt

PREFIX = "hm_feedback_"
AUDIENCE = "hypermesh-feedback-v1"
USE = "native-feedback-v1"
ROUTES = {("GET", "/v1/consent"), ("PUT", "/v1/consent"), ("POST", "/v1/reports")}


def verify(token, key, issuer, method, path):
    if (method, path) not in ROUTES or len(token) > 4096 or not token.startswith(PREFIX):
        raise ValueError("Invalid native feedback grant")
    claims = jwt.decode(token[len(PREFIX):], key, algorithms=["EdDSA"],
        issuer=issuer, audience=AUDIENCE,
        options={"require": ["sub", "tenant_id", "device_id", "jti", "iat", "exp", "token_use"]})
    if (claims["token_use"] != USE or claims["aud"] != AUDIENCE
            or type(claims["iat"]) is not int or type(claims["exp"]) is not int
            or not 0 < claims["exp"] - claims["iat"] <= 60):
        raise ValueError("Invalid native feedback scope")
    for field in ("sub", "tenant_id", "device_id", "jti"):
        value = claims[field]
        if not isinstance(value, str) or str(UUID(value)) != value or not UUID(value).int:
            raise ValueError("Invalid native feedback identity")
    return claims


def load_key(config):
    if config.get("native_feedback_enabled") is not True:
        return None
    import os
    from urllib.parse import urlsplit
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    issuer = config.get("native_feedback_issuer", "")
    if not isinstance(issuer, str):
        raise ValueError("Invalid native feedback issuer")
    url = urlsplit(issuer)
    if (not issuer.isascii() or len(issuer) > 200 or any(c.isspace() for c in issuer)
            or url.scheme != "https" or not url.hostname or url.username
            or url.password or url.query or url.fragment):
        raise ValueError("Invalid native feedback issuer")
    with open(os.environ["NATIVE_FEEDBACK_PUBLIC_KEY_FILE"], "rb") as source:
        pem = source.read(4097)
    if len(pem) > 4096:
        raise ValueError("Invalid native feedback key")
    key = serialization.load_pem_public_key(pem)
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("Invalid native feedback key")
    return key
