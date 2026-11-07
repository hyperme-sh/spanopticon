ALTER TABLE challenge ADD COLUMN method TEXT NOT NULL DEFAULT 'local-test-delivery';
ALTER TABLE challenge ADD COLUMN dispatch TEXT NOT NULL DEFAULT 'queued';
ALTER TABLE contact ADD COLUMN method TEXT NOT NULL DEFAULT 'local-test-delivery';
CREATE INDEX challenge_contact_created ON challenge(contact, created);
CREATE INDEX challenge_created ON challenge(created);
