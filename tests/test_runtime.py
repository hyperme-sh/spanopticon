import copy
import gzip
import io
import tarfile
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from deployment import bundle as b
from deployment import runtime as r
from deployment.registry import fetch_test
from runtime_fixture import ASSURANCE,runtime_fixture
from test_registry import FakeRegistry
from test_deployment_bundle import encode


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.fixture,self.compose,self.items=runtime_fixture(self.root/'source')
        self.layout=self.root/'download';self.output=self.root/'plan'
        fetch_test('ghcr.io/example/plane','test','0.24.0',self.layout,FakeRegistry(self.fixture))
        self.lock=b.verify(self.layout,self.fixture.reference)

    def test_both_assets_are_verified_and_no_archive_extraction(self):
        data=r.assets(self.layout,self.lock)
        self.assertEqual(set(data),{'compose','helm'})
        self.assertFalse((self.layout/'fixture').exists())

    def test_chart_expansion_and_unsafe_members_rejected(self):
        payloads=[gzip.compress(b'x'*(r.LIMIT+1))]
        for bad_name in ['../escape','chart/../escape','/absolute']:
            stream=io.BytesIO()
            with tarfile.open(fileobj=stream,mode='w:gz') as archive:
                item=tarfile.TarInfo(bad_name);item.size=1
                archive.addfile(item,io.BytesIO(b'x'))
            payloads.append(stream.getvalue())
        for raw in payloads:
            self.fixture.bundle['deployment_assets']['helm']=self.fixture.put(raw,b.ASSETS['helm'])
            self.fixture.refresh()
            lock=b.verify(self.fixture.root,self.fixture.reference)
            with self.assertRaises(ValueError):r.assets(self.fixture.root,lock)

    def test_compose_inventory_and_assurance(self):
        self.assertEqual(r.compose_check(self.compose,self.lock,ASSURANCE),self.compose)
        for mutate in ['image','build','assurance','health','inventory','hook']:
            document=copy.deepcopy(self.compose);service=document['services']['api']
            if mutate=='image':service['image']='ghcr.io/example/api:test'
            if mutate=='build':service['build']='.'
            if mutate=='assurance':service['environment']['SPANOPTICON_ASSURANCE_MODE']='stripe-kyc'
            if mutate=='health':service['healthcheck']['disable']=True
            if mutate=='inventory':document['services']['hidden']=service
            if mutate=='hook':service['post_start']=[{'command':'touch /tmp/side-effect'}]
            with self.subTest(mutate=mutate),self.assertRaises(ValueError):r.compose_check(document,self.lock,ASSURANCE)

    def test_normalized_compose_absolute_secret_targets(self):
        document=copy.deepcopy(self.compose)
        document['services']['api']['secrets']=[{'source':'verification-test-client','target':'/run/secrets/verification-test-client'}]
        r.compose_check(document,self.lock,ASSURANCE)
        document['services']['api']['secrets'][0]['target']='/run/secrets/other'
        with self.assertRaises(ValueError):r.compose_check(document,self.lock,ASSURANCE)

    def render(self,items=None):
        return encode({'apiVersion':'v1','kind':'List','items':self.items if items is None else items}).replace(b'__SPANOPTICON_NAMESPACE__',b'test-plane')

    def test_helm_inventory_namespace_images_probes_and_assurance(self):
        self.assertEqual(len(r.helm_check(self.render(),self.lock,ASSURANCE,'test-plane')),4)
        for mutate in ['image','namespace','cluster','hook','assurance','init','probe','secret']:
            items=copy.deepcopy(self.items);deployment=next(i for i in items if i['kind']=='Deployment')
            pod=deployment['spec']['template']['spec'];container=pod['containers'][0]
            if mutate=='image':container['image']='ghcr.io/example/api:test'
            if mutate=='namespace':deployment['metadata']['namespace']='production'
            if mutate=='cluster':items.append({'kind':'ClusterRole','metadata':{'name':'bad'}})
            if mutate=='hook':deployment['metadata']['annotations']={'helm.sh/hook':'pre-install'}
            if mutate=='assurance':container['env']=[]
            if mutate=='init':pod['initContainers']=[{'name':'hidden','image':'evil:latest'}]
            if mutate=='probe':container.pop('readinessProbe')
            if mutate=='secret':pod['volumes'][-1]['secret']['optional']=True
            with self.subTest(mutate=mutate),self.assertRaises(ValueError):r.helm_check(self.render(items),self.lock,ASSURANCE,'test-plane')

    def test_post_renderer_rejects_changed_install_or_upgrade(self):
        expected=self.root/'expected';raw=self.render()
        expected.write_bytes(r.canonical(r.kube_documents(raw)))
        script=Path(__file__).resolve().parents[1]/'scripts/helm_bundle_gate.py'
        ok=subprocess.run([sys.executable,str(script),str(expected)],input=raw,capture_output=True)
        self.assertEqual(ok.returncode,0,ok.stderr);self.assertEqual(ok.stdout,raw)
        bad=subprocess.run([sys.executable,str(script),str(expected)],input=raw.replace(b'ghcr.io/example/api',b'ghcr.io/evil/api'),capture_output=True)
        self.assertNotEqual(bad.returncode,0);self.assertEqual(bad.stdout,b'')

    def test_yaml_alias_duplicate_and_unsafe_objects_rejected(self):
        for raw in [b'kind: Service\nkind: Deployment',b'x: &x [1]\ny: *x',b'!!python/object/apply:os.system ["false"]']:
            with self.subTest(raw=raw),self.assertRaises(Exception):r.kube_documents(raw)

    def test_plan_cannot_apply_or_mutate_external_context(self):
        with patch.object(r,'command',return_value=self.render()) as run:
            result=r.deploy(self.layout,'helm','test-plane',ASSURANCE,self.output,context='kind-isolated')
        self.assertEqual(result['result'],'validated-plan');self.assertEqual(run.call_count,1)
        self.assertEqual(run.call_args.args[0][:2],['helm','template'])

    def test_helm_apply_disables_hooks_crds_and_uses_post_renderer(self):
        with patch.object(r,'command',side_effect=[self.render(),b'',b'{"info":{"status":"deployed"}}']) as run:
            result=r.deploy(self.layout,'helm','test-plane',ASSURANCE,self.output,context='kind-isolated',apply=True)
        install=run.call_args_list[1].args[0]
        for flag in ['--no-hooks','--skip-crds','--wait','--post-renderer','--kube-context']:self.assertIn(flag,install)
        self.assertEqual(result['result'],'tool-wait-succeeded')

    def test_failed_apply_never_writes_success_receipt(self):
        with patch.object(r,'command',side_effect=[self.render(),ValueError('failed')]),self.assertRaises(ValueError):
            r.deploy(self.layout,'helm','test-plane',ASSURANCE,self.output,context='kind-isolated',apply=True)
        self.assertFalse((self.output/'receipt.json').exists())

    def test_partial_download_and_tampered_cache_rejected(self):
        (self.layout/'selection.json').unlink()
        with self.assertRaises(OSError):r.selection(self.layout)
        self.assertFalse(self.output.exists())

    def test_prod_selection_rejected(self):
        path=self.layout/'selection.json';receipt=json.loads(path.read_text());receipt['channel']='prod';path.write_text(json.dumps(receipt))
        with patch.object(r,'command') as run,self.assertRaises(ValueError):
            r.deploy(self.layout,'helm','test-plane',ASSURANCE,self.output,context='kind-isolated',apply=True)
        run.assert_not_called();self.assertFalse(self.output.exists())

    def test_normalized_compose_cannot_swap_images(self):
        rendered=copy.deepcopy(self.compose);rendered['services']['api']['image']='ghcr.io/evil/api@sha256:'+'d'*64
        with patch.object(r,'command',return_value=encode(rendered)) as run,self.assertRaises(ValueError):
            r.deploy(self.layout,'compose','test-plane',ASSURANCE,self.output,context='default',apply=True)
        self.assertEqual(run.call_count,1);self.assertFalse((self.output/'receipt.json').exists())
