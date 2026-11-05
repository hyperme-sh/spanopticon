import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from deployment import bundle as b
from deployment.registry import GHCR,fetch_test
from test_deployment_bundle import BundleFixture,encode


class FakeRegistry:
    repository='ghcr.io/example/plane'
    def __init__(self,fixture):self.fixture=fixture;self.calls=[];self.bad_header=False;self.bad_blob=False
    def get(self,kind,identifier,limit):
        self.calls.append((kind,identifier))
        if kind=='manifests':
            raw=encode(self.fixture.manifest)
            digest='sha256:'+hashlib.sha256(raw).hexdigest()
            return raw,{'Docker-Content-Digest':'sha256:'+'0'*64 if self.bad_header else digest}
        raw=self.fixture.path({'digest':identifier}).read_bytes()
        return (b'wrong' if self.bad_blob else raw),{}


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.fixture=BundleFixture(self.root/'source')
        self.client=FakeRegistry(self.fixture);self.target=self.root/'download'

    def fetch(self,selector='test',version='0.24.0'):
        return fetch_test(self.client.repository,selector,version,self.target,self.client)

    def test_resolves_tag_once_and_fetches_remaining_content_by_digest(self):
        receipt=self.fetch()
        self.assertEqual(receipt['candidate'],'0.24.0-test.7')
        self.assertEqual([x for x in self.client.calls if x[0]=='manifests'],[('manifests','test')])
        self.assertTrue(all(x[1].startswith('sha256:') for x in self.client.calls[1:]))
        self.assertEqual(b.verify(self.target,receipt['bundle_reference'])['candidate'],receipt['candidate'])
        self.assertEqual(json.loads((self.target/'selection.json').read_text()),receipt)

    def test_exact_test_selector_is_allowed(self):self.assertEqual(self.fetch('0.24.0-test.7')['candidate'],'0.24.0-test.7')

    def test_stable_and_malformed_selectors_rejected_without_fetch(self):
        for selector in ['latest','prod','0.24.0','0.24.0-test.01','../test','0.25.0-test.7']:
            with self.subTest(selector=selector),self.assertRaises(ValueError):self.fetch(selector)
        self.assertFalse(self.client.calls);self.assertFalse(self.target.exists())

    def test_next_mismatch_or_mislabelled_tag_has_no_completed_receipt(self):
        for selector,version in [('test','0.25.0'),('0.24.0-test.8','0.24.0')]:
            target=self.root/('target-'+version+selector)
            with self.assertRaises(ValueError):fetch_test(self.client.repository,selector,version,target,self.client)
            self.assertFalse((target/'selection.json').exists())

    def test_digest_failures_leave_no_selection(self):
        for field in ['bad_header','bad_blob']:
            with self.subTest(field=field):
                setattr(self.client,field,True);target=self.root/field
                with self.assertRaises(ValueError):fetch_test(self.client.repository,'test','0.24.0',target,self.client)
                self.assertFalse((target/'selection.json').exists());setattr(self.client,field,False)

    def test_refuses_existing_destination(self):
        self.target.mkdir();(self.target/'keep').write_text('yes')
        with self.assertRaises(FileExistsError):self.fetch()
        self.assertEqual((self.target/'keep').read_text(),'yes');self.assertFalse(self.client.calls)

    def test_rejects_bad_descriptor_before_blob_requests(self):
        self.fixture.manifest['layers'][0]['size']=b.MAX_TOTAL+1
        with self.assertRaises(ValueError):self.fetch()
        self.assertEqual(len(self.client.calls),1)


class Response(io.BytesIO):
    def __init__(self,data,headers=None):super().__init__(data);self.headers=headers or {}


class TransportTests(unittest.TestCase):
    def setUp(self):self.client=GHCR('ghcr.io/example/plane','owner','secret-token')

    def challenge(self):return 'Bearer realm="https://ghcr.io/token",service="ghcr.io",scope="repository:example/plane:pull"'

    def test_authentication_is_fixed_to_ghcr_pull_scope(self):
        with patch.object(self.client,'_read',return_value=(b'{"token":"scoped-token"}',{})) as read:
            self.client._authenticate(self.challenge())
            self.assertTrue(read.call_args.args[0].startswith('https://ghcr.io/token?'))
            self.assertEqual(self.client.token,'scoped-token')
            self.assertTrue(read.call_args.args[1]['Authorization'].startswith('Basic '))
        for challenge in [self.challenge().replace('ghcr.io/token','evil.invalid/token'),self.challenge().replace(':pull',':push'),
                          self.challenge().replace('example/plane','other/plane')]:
            with patch.object(self.client,'_read') as read,self.assertRaises(ValueError):self.client._authenticate(challenge)
            read.assert_not_called()

    def test_401_challenge_retries_once_with_scoped_token(self):
        with patch.object(self.client,'_read',side_effect=[HTTPError('https://ghcr.io/',401,'',{'WWW-Authenticate':self.challenge()},None),
                                                        (b'{"token":"scoped"}',{}),(b'data',{})]) as read:
            self.assertEqual(self.client.get('manifests','test',100),(b'data',{}))
            self.assertEqual(read.call_args.args[1]['Authorization'],'Bearer scoped')

    def test_blob_redirect_strips_credentials(self):
        error=HTTPError('https://ghcr.io/',307,'',{'Location':'https://pkg-containers.githubusercontent.com/a?signature=signed'},None)
        with patch.object(self.client.opener,'open',side_effect=[error,Response(b'blob')]) as opened:
            self.assertEqual(self.client._read('https://ghcr.io/v2/example/plane/blobs/sha256:x',{'Authorization':'Bearer sensitive'},100,True)[0],b'blob')
            self.assertIsNone(opened.call_args.args[0].get_header('Authorization'))

    def test_untrusted_redirect_never_receives_credentials(self):
        for url in ['https://evil.invalid/a','http://pkg-containers.githubusercontent.com/a','https://ghcr.io@evil.invalid/a']:
            with patch.object(self.client.opener,'open',side_effect=HTTPError('https://ghcr.io/',307,'',{'Location':url},None)) as opened,self.assertRaises(ValueError):
                self.client._read('https://ghcr.io/a',{'Authorization':'secret'},100,True)
            self.assertEqual(opened.call_count,1)

    def test_download_bound_and_error_redaction(self):
        with patch.object(self.client.opener,'open',return_value=Response(b'x'*101)),self.assertRaises(ValueError):self.client._read('https://ghcr.io/a',{},100)
        with patch.object(self.client,'_read',side_effect=HTTPError('https://ghcr.io/?secret-token',403,'secret-token',{},None)):
            with self.assertRaises(ValueError) as caught:self.client.get('manifests','test',100)
            self.assertNotIn('secret-token',str(caught.exception))

    def test_no_credentials_needed_for_public_token(self):
        client=GHCR('ghcr.io/example/plane')
        with patch.object(client,'_read',return_value=(b'{"token":"public"}',{})) as read:
            client._authenticate(self.challenge());self.assertNotIn('Authorization',read.call_args.args[1])
