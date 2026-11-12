import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from startup.bootstrap import stage
from startup.command import Owners,endpoint,main
from startup.contract import fingerprint

ROOT=Path(__file__).resolve().parents[1]


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';self.source.mkdir()
        self.config=json.loads((ROOT/'startup/fixtures/mapping.json').read_text())
        (self.source/'mapping.json').write_text(json.dumps(self.config))
        for name in ('user','auth','acl','keycloak'):(self.source/(name+'.token')).write_text(name+'-private')
        self.env=stage(self.source,self.root/'private')
        self.env.update({name:'https://'+name.lower()+'.example.invalid' for name in ('STARTUP_USER_URL','STARTUP_AUTH_URL','STARTUP_ACL_URL','KEYCLOAK_INTERNAL_URL')})
        self.tool=self.root/'owner.py';self.tool.write_text('# owner artifact')
        self.env['STARTUP_KEYCLOAK_TOOL']=str(self.tool)
    def test_private_staging_and_invalid_configuration_before_writes(self):
        self.assertEqual((self.root/'private/mapping.json').stat().st_mode&0o777,0o600)
        (self.source/'mapping.json').write_text('{}')
        with self.assertRaises(ValueError):stage(self.source,self.root/'bad')
        self.assertFalse((self.root/'bad').exists())
    def test_duplicate_credentials_and_unverified_urls_refuse_before_calls(self):
        (self.root/'private/auth.token').write_text('user-private')
        with self.assertRaises(ValueError):Owners(self.env,self.root/'private/mapping.json')
        for url in ('http://service.internal','https://user:secret@example.invalid','https://host.invalid?token=secret'):
            with self.subTest(url=url),self.assertRaises(ValueError):endpoint(url)
    def test_http_uses_only_selected_owner_credential_and_mapping(self):
        calls=[]
        class Opener:
            def open(inner,request,timeout):calls.append(request);return io.BytesIO(b'{"status":"ok"}')
        owner=Owners(self.env,self.root/'private/mapping.json',opener=Opener())
        owner('auth',self.config,{})
        self.assertEqual(calls[0].headers['Authorization'],'Bearer auth-private')
        self.assertEqual(json.loads(calls[0].data),{'mapping_sha256':fingerprint(self.config)})
    def test_keycloak_gets_private_snapshot_and_no_peer_credentials(self):
        calls=[]
        def execute(command,**kwargs):
            calls.append(kwargs)
            self.assertEqual(json.loads(Path(command[command.index('--config')+1]).read_text()),self.config)
            self.assertNotIn('STARTUP_USER_TOKEN_FILE',kwargs['env'])
            self.assertNotIn('STARTUP_AUTH_TOKEN_FILE',kwargs['env'])
            self.assertEqual(Path(command[command.index('--bound-person')+1]).stat().st_mode&0o777,0o600)
            return SimpleNamespace(returncode=0,stdout='{"status":"reconciled"}')
        owner=Owners(self.env,self.root/'private/mapping.json',execute=execute)
        (self.root/'private/mapping.json').write_text('{}')
        owner('keycloak',self.config,{'auth':{'person_id':'one'},'acl':{'person_id':'one'}})
        self.assertEqual(len(calls),1)
    def test_disabled_command_does_not_create_state(self):
        with patch.dict(os.environ,{},clear=True),patch('sys.stderr',io.StringIO()):
            self.assertEqual(main(['--config',str(self.source/'mapping.json'),'--state-dir',str(self.root/'absent')]),1)
        self.assertFalse((self.root/'absent').exists())
    def test_templates_contain_no_binding_identifiers_or_secrets(self):
        rendered='\n'.join((ROOT/'startup'/name).read_text() for name in ('compose.yaml','deployment.env.example','Dockerfile'))
        for value in (self.config['binding']['sea_public_key'],self.config['binding']['entra']['tenant_id'],self.config['owner']['profile']['email'],'user-private'):
            self.assertNotIn(value,rendered)
        self.assertIn('STARTUP_IDENTITY_ENABLED:-0',rendered)
    def test_shared_contract_bytes(self):
        source=json.loads((ROOT/'startup/contract_source.json').read_text())
        self.assertEqual(hashlib.sha256((ROOT/'startup/contract.py').read_bytes()).hexdigest(),source['sha256'])
