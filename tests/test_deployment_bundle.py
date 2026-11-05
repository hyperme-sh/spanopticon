import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from deployment import bundle as b

ROOT = Path(__file__).resolve().parents[1]


def encode(value):
    return json.dumps(value, sort_keys=True).encode()


class BundleFixture:
    def __init__(self, root):
        self.root = root
        (root / 'blobs/sha256').mkdir(parents=True)
        compose = self.put(b'{"services":{}}', b.ASSETS['compose'])
        # Deliberately not a runnable chart: this verifier establishes bytes only.
        helm = self.put(b'opaque chart bytes', b.ASSETS['helm'])
        self.bundle = {'schema': 1, 'candidate': '0.24.0-test.7', 'source_revision': 'a'*40,
                       'components': {'api': {'image': 'ghcr.io/example/api@sha256:'+'b'*64,
                                             'version': '0.2.0', 'source_revision': 'c'*40}},
                       'deployment_assets': {'compose': compose, 'helm': helm}}
        self.manifest = {'schemaVersion': 2, 'mediaType': b.OCI, 'artifactType': b.BUNDLE,
                         'config': self.put(b'{}', b.EMPTY), 'layers': []}
        self.refresh()

    def put(self, raw, media_type):
        sha = hashlib.sha256(raw).hexdigest()
        (self.root / 'blobs/sha256' / sha).write_bytes(raw)
        return {'mediaType': media_type, 'digest': 'sha256:'+sha, 'size': len(raw)}

    def refresh(self):
        self.manifest['layers'] = [self.put(encode(self.bundle), b.BUNDLE), *self.bundle['deployment_assets'].values()]
        self.seal()

    def seal(self):
        self.reference = 'ghcr.io/example/plane@'+self.put(encode(self.manifest), b.OCI)['digest']

    def path(self, descriptor):
        return self.root / 'blobs/sha256' / descriptor['digest'].split(':')[1]


class DeploymentBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = BundleFixture(self.root)
        self.output = self.root / 'lock.json'

    def reject(self):
        with self.assertRaises((ValueError, OSError)):
            b.write_lock(self.root, self.fixture.reference, self.output)
        self.assertFalse(self.output.exists())

    def test_lock_preserves_candidate_embedded_versions_and_digests(self):
        lock = b.write_lock(self.root, self.fixture.reference, self.output)
        self.assertEqual(json.loads(self.output.read_text()), lock)
        self.assertEqual(lock['bundle_reference'], self.fixture.reference)
        self.assertEqual(lock['verification'], 'local-content-only')
        for key in ('candidate','source_revision','components','deployment_assets'):
            self.assertEqual(lock[key],self.fixture.bundle[key])
        self.assertNotIn('channel',lock)

    def test_cli_works_without_registry_or_cluster(self):
        result = subprocess.run([sys.executable, str(ROOT/'scripts/lock_deployment.py'),
                                 '--layout', str(self.root), '--reference', self.fixture.reference,
                                 '--output', str(self.output)], capture_output=True, text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(self.output.read_text())['candidate'],'0.24.0-test.7')

    def test_mutable_tags_and_untrusted_reference_forms_rejected(self):
        for ref in ['ghcr.io/example/plane:test','ghcr.io/example/plane:0.24.0-test.7',
                    'docker.io/example/plane@sha256:'+'a'*64,
                    'ghcr.io/example/../plane@sha256:'+'a'*64,
                    self.fixture.reference+'\n', self.fixture.reference.replace('sha256:','sha512:')]:
            with self.subTest(ref=ref), self.assertRaises(ValueError): b.verify(self.root,ref)

    def test_tampering_each_blob_rejected(self):
        descriptors = [self.fixture.manifest['config'], *self.fixture.manifest['layers'],
                       {'digest':self.fixture.reference.split('@')[1]}]
        for descriptor in descriptors:
            path=self.fixture.path(descriptor);raw=path.read_bytes()
            with self.subTest(digest=descriptor['digest']):
                path.write_bytes(bytes([raw[0]^1])+raw[1:]);self.reject();path.write_bytes(raw)

    def test_missing_blob_rejected(self):
        self.fixture.path(self.fixture.bundle['deployment_assets']['helm']).unlink()
        self.reject()

    def test_symlink_blob_rejected_even_with_matching_bytes(self):
        path=self.fixture.path(self.fixture.bundle['deployment_assets']['helm'])
        target=self.root/'external';path.rename(target);path.symlink_to(target)
        self.reject()

    def test_symlink_blob_directory_rejected(self):
        path=self.root/'blobs/sha256';target=self.root/'outside';path.rename(target);path.symlink_to(target)
        self.reject()

    def test_deployment_layer_cannot_be_substituted_for_declared_asset(self):
        self.fixture.manifest['layers'][1]=self.fixture.put(b'other compose',b.ASSETS['compose'])
        self.fixture.seal();self.reject()

    def test_descriptor_size_type_and_limits(self):
        original=copy.deepcopy(self.fixture.manifest)
        for size in [True,0,-1,b.MAX_TOTAL+1,3]:
            with self.subTest(size=size):
                self.fixture.manifest=copy.deepcopy(original)
                self.fixture.manifest['layers'][0]['size']=size
                self.fixture.seal();self.reject()

    def test_duplicate_missing_or_extra_layers(self):
        original=copy.deepcopy(self.fixture.manifest)
        for layers in [original['layers'][:2],original['layers']+[original['layers'][0]],
                       [original['layers'][0]]*3]:
            self.fixture.manifest=copy.deepcopy(original);self.fixture.manifest['layers']=layers
            self.fixture.seal();self.reject()

    def test_metadata_cannot_reference_external_blob_urls(self):
        self.fixture.manifest['layers'][0]['urls']=['https://example.invalid/blob']
        self.fixture.seal();self.reject()

    def test_bundle_schema_and_candidate_validation(self):
        original=copy.deepcopy(self.fixture.bundle)
        for field,value in [('schema',True),('schema',2),('candidate','0.24.0'),('candidate','0.24.0-test.01'),
                            ('source_revision','main'),('components',{}),('components',[]),('unknown',True)]:
            with self.subTest(field=field,value=value):
                self.fixture.bundle=copy.deepcopy(original);self.fixture.bundle[field]=value
                self.fixture.refresh();self.reject()

    def test_components_cannot_float_or_smuggle_fields(self):
        original=copy.deepcopy(self.fixture.bundle)
        for field,value in [('image','ghcr.io/example/api:test'),('source_revision','main'),('version',''),
                            ('version','0.2.0\n'),('command','run this')]:
            with self.subTest(field=field):
                self.fixture.bundle=copy.deepcopy(original)
                self.fixture.bundle['components']['api'][field]=value
                self.fixture.refresh();self.reject()

    def test_invalid_component_names(self):
        original=copy.deepcopy(self.fixture.bundle)
        for name in ['API','../api','api_1','x'*64,'']:
            self.fixture.bundle=copy.deepcopy(original)
            self.fixture.bundle['components'][name]=self.fixture.bundle['components'].pop('api')
            self.fixture.refresh();self.reject()

    def test_duplicate_json_keys_and_non_json_constants(self):
        for raw in [b'{"schema":1,"schema":2}',b'{"x":NaN}',b'{"x":Infinity}']:
            self.fixture.manifest['layers'][0]=self.fixture.put(raw,b.BUNDLE)
            self.fixture.seal();self.reject()

    def test_unsupported_oci_shapes(self):
        original=copy.deepcopy(self.fixture.manifest)
        for field,value in [('schemaVersion',True),('schemaVersion',1),('mediaType','application/vnd.oci.image.index.v1+json'),
                            ('artifactType','application/octet-stream'),('subject',{})]:
            self.fixture.manifest=copy.deepcopy(original);self.fixture.manifest[field]=value
            self.fixture.seal();self.reject()

    def test_existing_outputs_not_overwritten(self):
        self.output.write_text('keep')
        with self.assertRaises(FileExistsError): b.write_lock(self.root,self.fixture.reference,self.output)
        self.assertEqual(self.output.read_text(),'keep')
        self.output.unlink();target=self.root/'target';target.write_text('keep');self.output.symlink_to(target)
        with self.assertRaises(FileExistsError): b.write_lock(self.root,self.fixture.reference,self.output)
        self.assertEqual(target.read_text(),'keep')

    def test_invalid_cli_leaves_no_output(self):
        self.fixture.path(self.fixture.manifest['layers'][0]).write_bytes(b'changed')
        result=subprocess.run([sys.executable,str(ROOT/'scripts/lock_deployment.py'),'--layout',str(self.root),
                               '--reference',self.fixture.reference,'--output',str(self.output)],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Bundle rejected:',result.stderr)
        self.assertFalse(self.output.exists())
