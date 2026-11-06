import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from deployment import owner_image as image


class OwnerImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.blobs={}
        self.value=dict(schema=1,owner='hypermesh-gateway',source_revision='a'*40,run_id=1,artifact_id=2,
                        artifact_digest='sha256:'+'b'*64,destination='ghcr.io/hyperme-sh/spanopticon-hypermesh-gateway')
        config=self.blob(dict(os='linux',architecture='amd64',config={'Labels':{
            'org.opencontainers.image.source':'https://github.com/hyperme-sh/hypermesh-gateway',
            'org.opencontainers.image.revision':'a'*40}}),image.CONFIG)
        self.value['config_digest']=config['digest']
        self.manifest=dict(schemaVersion=2,config=config,layers=[self.blob({'payload':'layer'},'application/vnd.oci.image.layer.v1.tar')])
        self.write()

    def blob(self,value,media):
        data=json.dumps(value).encode();digest='sha256:'+hashlib.sha256(data).hexdigest()
        self.blobs['blobs/sha256/'+digest.split(':')[1]]=data
        return dict(mediaType=media,digest=digest,size=len(data))

    def write(self,extra=None):
        descriptor=self.blob(self.manifest,image.b.OCI);self.value['manifest_digest']=descriptor['digest']
        members={**self.blobs,'index.json':json.dumps(dict(schemaVersion=2,manifests=[descriptor])).encode(),**(extra or {})}
        self.archive=self.root/'image.tar'
        with tarfile.open(self.archive,'w') as tar:
            for name,data in members.items():
                info=tarfile.TarInfo(name);info.size=len(data);tar.addfile(info,io.BytesIO(data))
        self.value['archive_digest']=image.sha(self.archive)

    def test_real_graph(self):
        receipt=image.verify(self.value,self.archive)
        self.assertEqual(len(receipt['blobs']),3)

    def test_config_as_layer_does_not_qualify_runtime(self):
        original=copy.deepcopy(self.manifest['config'])
        self.manifest['config']=self.blob({'os':'unknown'},image.CONFIG)
        self.manifest['layers'].append(original);self.write()
        with self.assertRaisesRegex(ValueError,'runtime config absent'):image.verify(self.value,self.archive)

    def test_traversal_member_rejected(self):
        self.write({'../outside':b'x'})
        with self.assertRaisesRegex(ValueError,'Unsafe'):image.verify(self.value,self.archive)

    def test_changed_archive_rejected(self):
        with self.archive.open('ab') as stream:stream.write(b'x')
        with self.assertRaisesRegex(ValueError,'archive digest'):image.verify(self.value,self.archive)

    def test_wrong_destination_rejected(self):
        self.value['destination']='ghcr.io/other/image'
        with self.assertRaisesRegex(ValueError,'destination'):image.pin(self.value)

    def test_private_plane_artifact_rejected(self):
        value={**self.value,'owner':'hypermesh-plane','destination':'ghcr.io/hyperme-sh/spanopticon-hypermesh-plane'}
        repo={'id':4,'full_name':'hyperme-sh/hypermesh-plane'}
        run=dict(id=1,head_sha='a'*40,head_branch='main',event='push',path='.github/workflows/service.yml',status='completed',conclusion='success',repository=repo,head_repository=repo)
        with self.assertRaisesRegex(ValueError,'qualification'):image.provenance(value,run,{})

    def test_publish_refuses_pr_before_commands(self):
        spec=importlib.util.spec_from_file_location('owner_cli',Path(__file__).parents[1]/'scripts/owner_image.py')
        cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
        with patch.object(cli,'command') as execute:
            with self.assertRaisesRegex(ValueError,'Only the main'):cli.publish(self.value,self.archive,{})
            execute.assert_not_called()
