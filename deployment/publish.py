"""Publish already-qualified fleet bytes to GHCR's test channel only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.error import HTTPError
from deployment import bundle as b
from deployment.registry import GHCR, fetch_test

REPOSITORY = 'ghcr.io/hyperme-sh/spanopticon-fleet-test'
QUALIFIER = 'hyperme-sh/spanopticon/.github/workflows/qualify-fleet.yml@refs/heads/main'


def admitted(root, env):
    """The caller's successful qualification job is the trust boundary, not JSON alone."""
    b.require(env.get('GITHUB_REPOSITORY') == 'hyperme-sh/spanopticon'
              and env.get('GITHUB_REF') == 'refs/heads/main'
              and env.get('GITHUB_EVENT_NAME') == 'push'
              and env.get('GITHUB_WORKFLOW_REF') == QUALIFIER,
              'Only the main fleet qualification workflow may publish')
    run, attempt = env.get('GITHUB_RUN_ID', ''), env.get('GITHUB_RUN_ATTEMPT', '')
    b.require(b.matches(r'[1-9][0-9]*',run) and b.matches(r'[1-9][0-9]*',attempt), 'Invalid run identity')
    root = Path(root)
    raw = (root/'qualification.json').read_bytes()
    b.require(len(raw) <= b.MAX_METADATA, 'Qualification record too large')
    record = b.document(raw)
    b.keys(record, ('schema','source_revision','run_id','run_attempt','bundle_reference','receipts'), 'qualification')
    b.require(record['schema'] == 'spanopticon-fleet-qualification/v1'
              and record['source_revision'] == env.get('GITHUB_SHA')
              and record['run_id'] == run and record['run_attempt'] == attempt,
              'Qualification source/run identity mismatch')
    b.require(record['bundle_reference'].startswith(REPOSITORY+'@'), 'Unexpected publication repository')
    lock = b.verify(root/'oci',record['bundle_reference'])
    # Run IDs are allocated by GitHub before qualification. Re-runs keep the same
    # identity and may only reuse identical bytes; they never replace a candidate.
    b.require(lock['candidate'] == '0.24.0-test.'+run
              and lock['source_revision'] == record['source_revision'], 'Candidate/source mismatch')
    receipts = record['receipts']
    b.keys(receipts, ('compose','helm'), 'runtime receipts')
    for engine, receipt in receipts.items():
        b.require(isinstance(receipt,dict) and receipt.get('engine') == engine
                  and receipt.get('channel') == 'test'
                  and receipt.get('result') == 'tool-wait-succeeded'
                  and receipt.get('bundle_reference') == lock['bundle_reference']
                  and receipt.get('candidate') == lock['candidate']
                  and receipt.get('images') == {n:c['image'] for n,c in lock['components'].items()},
                  'Runtime receipt does not qualify these exact bytes')
        for key in ('rendered_sha256','assurance_config_sha256','observation_sha256'):
            b.require(b.matches(r'[0-9a-f]{64}',receipt.get(key)), 'Missing runtime observation identity')
    b.require(receipts['compose']['assurance_config_sha256'] == receipts['helm']['assurance_config_sha256'],
              'Runtime engines used different assurance settings')
    return lock


class Registry(GHCR):
    def optional_manifest(self, selector):
        try:
            raw, headers = self.get('manifests',selector,b.MAX_METADATA)
        except MissingManifest:
            return None
        digest = 'sha256:'+hashlib.sha256(raw).hexdigest()
        b.require(headers.get('Docker-Content-Digest') == digest, 'Remote manifest digest mismatch')
        return digest

    def _read(self, url, headers, limit, redirect=False):
        try:
            return super()._read(url,headers,limit,redirect)
        except HTTPError as error:
            if error.code == 404 and '/manifests/' in url:
                error.close()
                raise MissingManifest() from None
            raise


class MissingManifest(Exception):
    pass


def run(args, *, input_bytes=None):
    # No child response bodies, tokens or expanded configuration in diagnostics.
    result = subprocess.run(args,input=input_bytes,stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,timeout=600,check=False)
    b.require(result.returncode == 0, 'ORAS publication operation failed')


def publish(root, env=None, client=None, execute=run):
    env = dict(os.environ) if env is None else env
    root = Path(root).resolve()
    lock = admitted(root,env)  # No credentials, registry writes or login before admission.
    token, user = env.get('GHCR_TOKEN'),env.get('GHCR_USERNAME')
    b.require(token and user, 'GHCR credentials required')
    client = client or Registry(REPOSITORY,user,token)
    b.require(client.repository == REPOSITORY, 'Registry client repository mismatch')
    reference, candidate = lock['bundle_reference'],lock['candidate']
    digest = reference.rsplit('@',1)[1]
    with tempfile.TemporaryDirectory(prefix='fleet-test-publish-') as directory:
        scratch = Path(directory)
        existing = client.optional_manifest(candidate)
        b.require(existing is None or existing == digest, 'Immutable test candidate already has different bytes')
        current = client.optional_manifest('test')
        previous = None
        if current:
            # Verify the current channel fully before deciding whether it may advance.
            previous = fetch_test(REPOSITORY,'test','0.24.0',scratch/'previous',client)
            b.require(previous['bundle_reference'] == REPOSITORY+'@'+current, 'Test channel changed during publication')
            old_number = int(previous['candidate'].rsplit('.',1)[1])
            number = int(candidate.rsplit('.',1)[1])
            b.require(old_number != number or current == digest, 'Candidate counter collision')
        config = str(scratch/'auth.json')
        execute(['oras','login','ghcr.io','--registry-config',config,'--username',user,'--password-stdin'],input_bytes=token.encode())
        if existing is None:
            execute(['oras','cp','--from-oci-layout',str(root/'oci')+'@'+digest,
                     REPOSITORY+':'+candidate,'--to-registry-config',config])
        # Read every blob back before exposing a moving channel alias.
        fetched = fetch_test(REPOSITORY,candidate,'0.24.0',scratch/'candidate',client)
        b.require(fetched['bundle_reference'] == reference, 'Published candidate differs from qualified bytes')
        advance = previous is None or int(previous['candidate'].rsplit('.',1)[1]) <= int(candidate.rsplit('.',1)[1])
        b.require(client.optional_manifest('test') == current, 'Test channel changed; retry under the shared publication lock')
        if advance and current != digest:
            execute(['oras','tag',reference,'test','--registry-config',config])
        if advance:
            fetched = fetch_test(REPOSITORY,'test','0.24.0',scratch/'channel',client)
            b.require(fetched['bundle_reference'] == reference, 'Test alias read-back differs')
    return {'schema':1,'repository':REPOSITORY,'candidate':candidate,'bundle_reference':reference,
            'source_revision':lock['source_revision'],'run_id':env['GITHUB_RUN_ID'],
            'test_channel_advanced':advance,'verification':'all published bundle bytes read back and verified',
            'stable_published':False}
