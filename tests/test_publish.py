import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import yaml
from deployment import bundle as b
from deployment.publish import admitted, publish, Registry, MissingManifest, REPOSITORY, QUALIFIER
from test_deployment_bundle import BundleFixture,encode


class MemoryRegistry:
    repository=REPOSITORY
    def __init__(self,fixture):
        self.fixture=fixture;self.tags={};self.calls=[];self.corrupt=False
    def optional_manifest(self,tag):
        self.calls.append(('lookup',tag))
        item=self.tags.get(tag)
        return item.reference.rsplit('@',1)[1] if item else None
    def get(self,kind,identifier,limit):
        self.calls.append((kind,identifier))
        if kind=='manifests':
            item=self.tags[identifier];raw=encode(item.manifest)
            return raw,{'Docker-Content-Digest':'sha256:'+hashlib.sha256(raw).hexdigest()}
        for item in [self.fixture,*self.tags.values()]:
            path=item.path({'digest':identifier})
            if path.exists():return (b'corrupt' if self.corrupt else path.read_bytes()),{}
        raise AssertionError('unknown blob')


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.fixture=BundleFixture(self.root/'oci')
        self.reference=REPOSITORY+'@'+self.fixture.reference.rsplit('@',1)[1]
        self.env={'GITHUB_REPOSITORY':'hyperme-sh/spanopticon','GITHUB_REF':'refs/heads/main',
                  'GITHUB_EVENT_NAME':'push','GITHUB_WORKFLOW_REF':QUALIFIER,'GITHUB_SHA':'a'*40,
                  'GITHUB_RUN_ID':'7','GITHUB_RUN_ATTEMPT':'1','GHCR_USERNAME':'test','GHCR_TOKEN':'secret'}
        receipt={'channel':'test','result':'tool-wait-succeeded','bundle_reference':self.reference,
                 'candidate':'0.24.0-test.7','images':{'api':self.fixture.bundle['components']['api']['image']},
                 'rendered_sha256':'d'*64,'observation_sha256':'e'*64,'assurance_config_sha256':'f'*64}
        self.record={'schema':'spanopticon-fleet-qualification/v1','source_revision':'a'*40,
                     'run_id':'7','run_attempt':'1','bundle_reference':self.reference,
                     'receipts':{e:{**receipt,'engine':e} for e in ('compose','helm')}}
        self.save();self.client=MemoryRegistry(self.fixture);self.commands=[]
    def save(self): (self.root/'qualification.json').write_text(json.dumps(self.record))
    def execute(self,args,**kwargs):
        self.commands.append(args)
        if args[1]=='cp':self.client.tags['0.24.0-test.7']=self.fixture
        if args[1]=='tag':self.client.tags['test']=self.fixture
    def publish(self):return publish(self.root,self.env,self.client,self.execute)
    def test_copies_qualified_bytes_then_moves_test_after_full_readback(self):
        result=self.publish()
        self.assertEqual([c[1] for c in self.commands],['login','cp','tag'])
        self.assertEqual(self.commands[1][-3],REPOSITORY+':0.24.0-test.7')
        self.assertIn(str(self.root/'oci')+'@'+self.reference.rsplit('@',1)[1],self.commands[1])
        self.assertFalse(result['stable_published']);self.assertTrue(result['test_channel_advanced'])
        self.assertEqual(self.client.tags['test'],self.fixture)
    def test_identical_retry_never_rewrites_candidate_or_alias(self):
        self.publish();self.commands=[];self.publish()
        self.assertEqual([c[1] for c in self.commands],['login'])
    def test_existing_candidate_with_different_bytes_refused_before_login(self):
        other=BundleFixture(self.root/'other');other.bundle['source_revision']='d'*40;other.refresh()
        self.client.tags['0.24.0-test.7']=other
        with self.assertRaises(ValueError):self.publish()
        self.assertFalse(self.commands)
    def test_old_run_does_not_move_channel_backwards(self):
        newer=BundleFixture(self.root/'newer');newer.bundle['candidate']='0.24.0-test.8';newer.refresh()
        self.client.tags['test']=newer
        self.assertFalse(self.publish()['test_channel_advanced'])
        self.assertNotIn('tag',[c[1] for c in self.commands]);self.assertIs(self.client.tags['test'],newer)
    def test_readback_failure_never_moves_test(self):
        self.client.corrupt=True
        with self.assertRaises(ValueError):self.publish()
        self.assertNotIn('tag',[c[1] for c in self.commands]);self.assertNotIn('test',self.client.tags)
    def test_auth_or_transport_error_is_not_absence(self):
        with patch.object(self.client,'optional_manifest',side_effect=ValueError('unauthorized')):
            with self.assertRaises(ValueError):self.publish()
        self.assertFalse(self.commands)
    def test_untrusted_workflow_branch_event_repo_and_run_rejected_before_registry(self):
        for key,value in [('GITHUB_REF','refs/pull/7/merge'),('GITHUB_EVENT_NAME','pull_request'),
                          ('GITHUB_REPOSITORY','other/spanopticon'),('GITHUB_WORKFLOW_REF',QUALIFIER.replace('qualify-fleet','validate')),
                          ('GITHUB_RUN_ID','8'),('GITHUB_RUN_ATTEMPT','2'),('GITHUB_SHA','b'*40)]:
            with self.subTest(key=key),self.assertRaises(ValueError):publish(self.root,{**self.env,key:value},self.client,self.execute)
        self.assertFalse(self.commands);self.assertFalse(self.client.calls)
    def test_missing_or_mismatched_runtime_receipts_refused(self):
        original=copy.deepcopy(self.record)
        for key,value in [('result','validated-plan'),('images',{}),('observation_sha256',None),
                          ('candidate','0.24.0-test.8'),('assurance_config_sha256','a'*64)]:
            self.record=copy.deepcopy(original);self.record['receipts']['helm'][key]=value;self.save()
            with self.subTest(key=key),self.assertRaises(ValueError):self.publish()
        self.record=copy.deepcopy(original);del self.record['receipts']['helm'];self.save()
        with self.assertRaises(ValueError):self.publish()
        self.assertFalse(self.commands)
    def test_stable_or_other_line_candidate_refused(self):
        for candidate in ('0.24.0','0.25.0-test.7'):
            self.fixture.bundle['candidate']=candidate;self.fixture.refresh()
            self.record['bundle_reference']=REPOSITORY+'@'+self.fixture.reference.rsplit('@',1)[1];self.save()
            with self.assertRaises(ValueError):self.publish()
        self.assertFalse(self.commands)
    def test_corrupted_local_blob_refused_before_login(self):
        self.fixture.path(self.fixture.bundle['deployment_assets']['compose']).write_bytes(b'bad')
        with self.assertRaises(ValueError):self.publish()
        self.assertFalse(self.commands)
    def test_concurrent_channel_change_detected_before_alias_write(self):
        values=[None,None,'sha256:'+'a'*64]
        with patch.object(self.client,'optional_manifest',side_effect=values):
            with self.assertRaises(ValueError):self.publish()
        self.assertNotIn('tag',[c[1] for c in self.commands])
    def test_transport_only_manifest_404_is_missing(self):
        client=Registry(REPOSITORY)
        with patch('deployment.registry.GHCR._read',side_effect=HTTPError('https://ghcr.io/',404,'',{},None)):
            self.assertIsNone(client.optional_manifest('test'))
        for code in (401,403,429,500):
            with patch('deployment.registry.GHCR._read',side_effect=HTTPError('https://ghcr.io/',code,'',{},None)):
                with self.assertRaises(ValueError):client.optional_manifest('test')
    def test_workflow_has_same_run_artifact_and_serialized_test_only_writes(self):
        root=Path(__file__).resolve().parents[1]
        workflow=yaml.load((root/'.github/workflows/publish-fleet-test.yml').read_text(),Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow['on']),{'workflow_call'})
        job=workflow['jobs']['publish'];self.assertNotIn('environment',job)
        self.assertEqual(job['permissions']['contents'],'read');self.assertEqual(job['permissions']['packages'],'write')
        self.assertEqual(job['concurrency']['cancel-in-progress'],'false')
        download=next(s for s in job['steps'] if s.get('uses','').startswith('actions/download-artifact@'))
        self.assertEqual(download['with']['name'],'fleet-test-bundle-${{ github.run_attempt }}')
        self.assertNotIn('run-id',download['with'])


if __name__=='__main__':unittest.main()
