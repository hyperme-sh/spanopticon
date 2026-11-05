import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from deployment.generate import prepare,write
from runtime_fixture import ASSURANCE,HEALTH


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.metadata={'schema':1,'candidate':'0.24.0-test.7','source_revision':'a'*40,'components':{
            name:{'image':'ghcr.io/example/'+name+'@sha256:'+'b'*64,'version':'0.2.0','source_revision':'c'*40}
            for name in ('api','worker')}}
        self.runtime={name:{'healthcheck':HEALTH} for name in self.metadata['components']}

    def test_complete_multi_adapter_assets_preserve_images_and_assurance(self):
        raw,chart=prepare(self.metadata,self.runtime,ASSURANCE);compose=json.loads(raw)
        self.assertEqual(set(compose['services']),{'api','worker'})
        for name,service in compose['services'].items():
            self.assertEqual(service['image'],self.metadata['components'][name]['image'])
            self.assertEqual(service['healthcheck']['test'],HEALTH)
            self.assertEqual(service['environment']['SPANOPTICON_ASSURANCE_MODE'],'verification')
            self.assertNotIn('ports',service)
        with tarfile.open(fileobj=io.BytesIO(chart),mode='r:gz') as archive:
            items=json.load(archive.extractfile('spanopticon-plane/resources.json'))['items']
            self.assertEqual({i['metadata']['name'] for i in items if i['kind']=='Deployment'},{'api','worker'})
            self.assertFalse(any(i['kind']=='Namespace' for i in items))
        self.assertEqual((raw,chart),prepare(self.metadata,self.runtime,ASSURANCE))

    def test_missing_health_or_assurance_fails_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            for runtime,assurance in [({},ASSURANCE),(self.runtime,None)]:
                with self.assertRaises(ValueError):write(self.metadata,runtime,assurance,Path(directory)/'out')
                self.assertFalse((Path(directory)/'out').exists())

    def test_existing_output_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileExistsError):write(self.metadata,self.runtime,ASSURANCE,Path(directory))
