import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import yaml
from deployment import bundle as b
from deployment.qualify import inputs,assemble_input,qualify
from deployment.publish import admitted,QUALIFIER
from runtime_fixture import runtime_fixture,ASSURANCE


class QualifyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'inputs';self.source.mkdir()
        self.fixture,self.compose,self.items=runtime_fixture(self.root/'fixture')
        self.declaration={'schema':1,'components':self.fixture.bundle['components'],'assurance':ASSURANCE}
        (self.source/'test.json').write_text(json.dumps(self.declaration))
        for key,name in [('compose','compose.json'),('helm','chart.tgz')]:
            (self.source/name).write_bytes(self.fixture.path(self.fixture.bundle['deployment_assets'][key]).read_bytes())
        self.env={'GITHUB_REPOSITORY':'hyperme-sh/spanopticon','GITHUB_REF':'refs/heads/main',
                  'GITHUB_EVENT_NAME':'push','GITHUB_WORKFLOW_REF':QUALIFIER,'GITHUB_SHA':'a'*40,
                  'GITHUB_RUN_ID':'7','GITHUB_RUN_ATTEMPT':'1',
                  'FLEET_TEST_CREDENTIALS':json.dumps({'verification-test-client':'synthetic-secret'})}
        self.output=self.root/'out';self.calls=[];self.applies=[]
        for item in self.items:item['metadata']['namespace']='fleet-validation'
        self.render=patch('deployment.qualify.command',return_value=json.dumps({'kind':'List','items':self.items}).encode())
        self.render.start();self.addCleanup(self.render.stop)
    def execute(self,args):self.calls.append(args)
    def apply(self,layout,engine,name,assurance,output,**kwargs):
        self.applies.append((engine,name,kwargs))
        output.mkdir()
        if engine=='compose':(output/'compose.resolved.json').write_text('{}')
        variable='SPANOPTICON_SECRET_VERIFICATION_TEST_CLIENT_FILE'
        self.assertEqual(Path(os.environ[variable]).read_text(),'synthetic-secret')
        lock=json.loads((layout/'deployment.lock.json').read_text())
        return {'schema':1,'channel':'test','engine':engine,'name':name,'context':kwargs['context'],
                'bundle_reference':lock['bundle_reference'],'candidate':lock['candidate'],
                'images':{n:c['image'] for n,c in lock['components'].items()},
                'result':'tool-wait-succeeded','rendered_sha256':'d'*64,
                'assurance_config_sha256':'e'*64,'observation_sha256':'f'*64}
    def run_qualification(self):
        return qualify(self.source,self.output,self.env,kube_context='kind-spanopticon-fleet-test',apply=self.apply,execute=self.execute)
    def test_binds_same_bytes_and_both_engines_then_cleans_without_volume_deletion(self):
        record=self.run_qualification()
        self.assertEqual([e for e,_,_ in self.applies],['compose','helm','helm'])
        self.assertEqual(admitted(self.output,self.env)['bundle_reference'],record['bundle_reference'])
        self.assertTrue(any(c[-1]=='down' for c in self.calls));self.assertFalse(any('-v' in c for c in self.calls))
        self.assertTrue(any('delete' in c and 'namespace' in c for c in self.calls))
        self.assertNotIn('SPANOPTICON_SECRET_VERIFICATION_TEST_CLIENT_FILE',os.environ)
        for path in self.output.rglob('*'):
            if path.is_file():self.assertNotIn(b'synthetic-secret',path.read_bytes())
    def test_failure_never_emits_success_and_cleans_both_engines(self):
        normal=self.apply
        def fail(*args,**kwargs):
            if args[1]=='helm':raise ValueError('fixture failed')
            return normal(*args,**kwargs)
        with self.assertRaises(ValueError):qualify(self.source,self.output,self.env,kube_context='kind-spanopticon-fleet-test',apply=fail,execute=self.execute)
        self.assertFalse((self.output/'qualification.json').exists())
        self.assertTrue(any(c[-1]=='down' for c in self.calls))
        self.assertTrue(any('delete' in c for c in self.calls))
    def test_failed_cleanup_never_emits_success_or_leaves_secret_environment(self):
        def fail(args):
            if args[-1]=='down':raise ValueError('cleanup failed')
            self.calls.append(args)
        with self.assertRaises(ValueError):qualify(self.source,self.output,self.env,kube_context='kind-spanopticon-fleet-test',apply=self.apply,execute=fail)
        self.assertFalse((self.output/'qualification.json').exists())
        self.assertTrue(any('delete' in c for c in self.calls))
        self.assertNotIn('SPANOPTICON_SECRET_VERIFICATION_TEST_CLIENT_FILE',os.environ)
    def test_missing_or_extra_credentials_prevent_runtime_calls(self):
        self.env['FLEET_TEST_CREDENTIALS']='{}'
        with self.assertRaises(ValueError):self.run_qualification()
        self.assertFalse(self.calls);self.assertFalse(self.applies)
    def test_untrusted_event_or_context_fails_before_any_runtime_or_output(self):
        for changes,context in [({'GITHUB_EVENT_NAME':'pull_request'},'kind-spanopticon-fleet-test'),({},'production')]:
            with self.assertRaises(ValueError):qualify(self.source,self.output,{**self.env,**changes},kube_context=context,apply=self.apply,execute=self.execute)
        self.assertFalse(self.output.exists());self.assertFalse(self.calls)
    def test_asset_validation_rejects_image_substitution_before_runtime(self):
        self.compose['services']['api']['image']='ghcr.io/example/other@sha256:'+'a'*64
        (self.source/'compose.json').write_text(json.dumps(self.compose))
        with self.assertRaises(ValueError):self.run_qualification()
        self.assertFalse(self.calls)
    def test_validate_only_needs_no_credentials_and_creates_no_qualification(self):
        declaration,reference=assemble_input(self.source,self.output,self.env)
        self.assertEqual(declaration,self.declaration)
        self.assertEqual(b.verify(self.output,reference)['candidate'],'0.24.0-test.7')
        self.assertFalse((self.output/'qualification.json').exists());self.assertFalse(self.calls)
    def test_missing_inventory_is_not_a_fixture_fallback(self):
        (self.source/'test.json').unlink()
        with self.assertRaises(FileNotFoundError):inputs(self.source)
    def test_workflow_no_pr_secrets_or_publication_and_success_dependency(self):
        root=Path(__file__).resolve().parents[1]
        workflow=yaml.load((root/'.github/workflows/qualify-fleet.yml').read_text(),Loader=yaml.BaseLoader)
        jobs=workflow['jobs']
        self.assertIn("github.event_name == 'push'",jobs['qualify']['if'])
        self.assertNotIn('secrets.',json.dumps(jobs['validate']))
        self.assertEqual(jobs['publish']['needs'],'qualify')
        self.assertEqual(jobs['publish']['permissions']['packages'],'write')
        for name,job in jobs.items():self.assertNotIn('environment',job)
        self.assertIn('No qualification or publication occurred',json.dumps(jobs['inventory']))


if __name__=='__main__':unittest.main()
