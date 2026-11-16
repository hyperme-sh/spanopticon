CREATE SCHEMA feedback;
CREATE TABLE feedback.schema_version (singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), version integer NOT NULL);
INSERT INTO feedback.schema_version VALUES(true,1);
CREATE TABLE feedback.member (
 tenant uuid NOT NULL, issuer text NOT NULL, subject text NOT NULL,
 role text NOT NULL CHECK (role IN ('submitter','reviewer')), enabled boolean NOT NULL DEFAULT true,
 PRIMARY KEY (tenant,issuer,subject)
);
CREATE TABLE feedback.consent (
 tenant uuid NOT NULL, issuer text NOT NULL, subject text NOT NULL, device text NOT NULL,
 revision bigint NOT NULL CHECK (revision > 0), enabled boolean NOT NULL,
 notice text NOT NULL, purpose text NOT NULL, changed_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant,issuer,subject,device),
 FOREIGN KEY(tenant,issuer,subject) REFERENCES feedback.member(tenant,issuer,subject)
);
CREATE TABLE feedback.consent_history (
 tenant uuid NOT NULL, issuer text NOT NULL, subject text NOT NULL, device text NOT NULL,
 revision bigint NOT NULL, enabled boolean NOT NULL, notice text NOT NULL, purpose text NOT NULL,
 changed_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz,
 PRIMARY KEY(tenant,issuer,subject,device,revision)
);
CREATE TABLE feedback.report (
 tenant uuid NOT NULL, id uuid NOT NULL, issuer text NOT NULL, subject text NOT NULL,
 device text NOT NULL, request_id uuid NOT NULL, consent_revision bigint NOT NULL,
 category text NOT NULL CHECK(category IN ('bug','feature')), component text NOT NULL, repository text NOT NULL,
 title text NOT NULL, body text NOT NULL, fingerprint text NOT NULL,
 state text NOT NULL CHECK(state IN ('pending','spam','duplicate','accepted','rejected','publishing','published','unknown','deleted')),
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
 closed_at timestamptz, issue_number bigint, cleanup_after timestamptz NOT NULL DEFAULT now(), external_pending boolean NOT NULL DEFAULT false,
 PRIMARY KEY(tenant,id), UNIQUE(tenant,issuer,subject,device,request_id),
 FOREIGN KEY(tenant,issuer,subject) REFERENCES feedback.member(tenant,issuer,subject)
);
CREATE INDEX report_dedup ON feedback.report(tenant,fingerprint);
CREATE INDEX report_expiry ON feedback.report(tenant,expires_at);
CREATE TABLE feedback.decision (
 tenant uuid NOT NULL, report_id uuid NOT NULL, reviewer_issuer text NOT NULL, reviewer_subject text NOT NULL,
 disposition text NOT NULL CHECK(disposition IN ('accepted','rejected')), decided_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant,report_id), FOREIGN KEY(tenant,report_id) REFERENCES feedback.report(tenant,id) ON DELETE CASCADE,
 FOREIGN KEY(tenant,reviewer_issuer,reviewer_subject) REFERENCES feedback.member(tenant,issuer,subject)
);
CREATE TABLE feedback.erasure (
 tenant uuid NOT NULL, issuer text NOT NULL, subject text NOT NULL,
 requested_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL DEFAULT now()+interval '24 months',
 PRIMARY KEY(tenant,issuer,subject)
);
DO $$ DECLARE name text; BEGIN
 FOREACH name IN ARRAY ARRAY['member','consent','consent_history','report','decision','erasure'] LOOP
  EXECUTE format('ALTER TABLE feedback.%I ENABLE ROW LEVEL SECURITY',name);
  EXECUTE format('ALTER TABLE feedback.%I FORCE ROW LEVEL SECURITY',name);
  EXECUTE format('CREATE POLICY tenant_scope ON feedback.%I USING (tenant = nullif(current_setting(''feedback.tenant'',true),'''')::uuid) WITH CHECK (tenant = nullif(current_setting(''feedback.tenant'',true),'''')::uuid)',name);
 END LOOP;
END $$;
