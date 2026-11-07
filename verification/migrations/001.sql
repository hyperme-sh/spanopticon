CREATE TABLE challenge (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    request_key TEXT NOT NULL,
    contact TEXT NOT NULL,
    revision INTEGER NOT NULL,
    digest TEXT NOT NULL,
    created INTEGER NOT NULL,
    expires INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'pending',
    delivered INTEGER NOT NULL DEFAULT 0,
    UNIQUE(scope, request_key)
);
CREATE INDEX challenge_scope_created ON challenge(scope, created);
CREATE TABLE contact (
    scope TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    address TEXT NOT NULL,
    verified_until INTEGER NOT NULL DEFAULT 0
);
