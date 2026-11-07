# Contact verification core

The first extracted component checks a single contact challenge against its tenant/subject scope, contact revision, expiry, attempt budget and keyed digest. It uses the Python standard library. Run its compatibility cases with `python3 -m unittest discover -s tests -p test_verification_challenge.py`.

Callers must derive the current scope from an authenticated principal, select the appropriate challenge method and hold a database lock or equivalent atomic transaction while reading and persisting the result. A successful attempt consumes the challenge. The caller records evidence only after committing that transition. Supply keys by challenge key ID and configure the attempt budget in the service.

HTTP endpoints, migrations, a delivery worker, local delivery adapter and evidence lifecycle are subsequent parts of the service extraction.
