"""Read-only GHCR transport and single-resolution test-channel fetching."""
import base64
import hashlib
import json
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

from deployment import bundle as b


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GHCR:
    """Credentials are sent only to GHCR's fixed token endpoint, never redirects."""
    def __init__(self, repository, username=None, password=None):
        b.require(b.matches(b.REPOSITORY, repository), 'Expected ghcr.io/owner/repository')
        b.require(bool(username) == bool(password), 'GHCR_USERNAME and GHCR_TOKEN must be supplied together')
        self.repository = repository
        self.path = repository.removeprefix('ghcr.io/')
        self.username, self.password = username, password
        self.token = None
        self.opener = build_opener(NoRedirect())

    def _read(self, url, headers, limit, redirect=False):
        # Never include request URLs, signed queries, credentials or response bodies in errors.
        for _ in range(4):
            try:
                with self.opener.open(Request(url, headers=headers), timeout=30) as response:
                    data = response.read(limit + 1)
                    b.require(len(data) <= limit, 'Registry response exceeds size limit')
                    return data, response.headers
            except HTTPError as error:
                status, response_headers = error.code, error.headers
                error.close()
                if redirect and status in (301, 302, 303, 307, 308):
                    location = response_headers.get('Location', '')
                    parsed = urlsplit(location)
                    b.require(parsed.scheme == 'https' and parsed.hostname == 'pkg-containers.githubusercontent.com'
                              and parsed.port in (None, 443) and parsed.username is None and parsed.password is None
                              and not parsed.fragment, 'Untrusted registry blob redirect')
                    url, headers = location, {'Accept': 'application/octet-stream'}
                    continue
                raise HTTPError('https://ghcr.io/', status, 'Registry request failed', response_headers, None) from None
            except (URLError, TimeoutError):
                raise ValueError('Registry connection failed') from None
        raise ValueError('Registry redirect limit exceeded')

    def _authenticate(self, challenge):
        expected = f'repository:{self.path}:pull'
        b.require(isinstance(challenge, str) and challenge.startswith('Bearer '), 'Unsupported GHCR authentication challenge')
        fields = dict(re.findall(r'([a-z]+)="([^"\r\n]*)"', challenge[7:]))
        b.require(fields == {'realm': 'https://ghcr.io/token', 'service': 'ghcr.io', 'scope': expected},
                  'Unexpected GHCR authentication scope or endpoint')
        headers = {'Accept': 'application/json'}
        if self.username:
            encoded = base64.b64encode(f'{self.username}:{self.password}'.encode()).decode()
            headers['Authorization'] = 'Basic ' + encoded
        raw, _ = self._read('https://ghcr.io/token?' + urlencode({'service': 'ghcr.io', 'scope': expected}), headers, b.MAX_METADATA)
        token = b.document(raw).get('token')
        b.require(isinstance(token, str) and 0 < len(token) < 32768 and not any(c.isspace() for c in token), 'Invalid registry token')
        self.token = token

    def get(self, kind, identifier, limit):
        b.require(kind in ('manifests', 'blobs'), 'Invalid registry resource kind')
        b.require(b.matches(b.DIGEST, identifier) or (kind == 'manifests' and b.matches(r'test|'+b.VERSION+r'-test\.(0|[1-9][0-9]*)', identifier)),
                  'Only test selectors and immutable digests are supported')
        url = f'https://ghcr.io/v2/{self.path}/{kind}/{identifier}'
        for attempt in range(2):
            headers = {'Accept': b.OCI if kind == 'manifests' else 'application/octet-stream'}
            if self.token:
                headers['Authorization'] = 'Bearer ' + self.token
            try:
                return self._read(url, headers, limit, redirect=kind == 'blobs')
            except HTTPError as error:
                error.close()
                if error.code != 401 or attempt:
                    raise ValueError(f'GHCR {kind} request failed (HTTP {error.code})') from None
                try:
                    self._authenticate(error.headers.get('WWW-Authenticate'))
                except HTTPError as auth_error:
                    auth_error.close()
                    raise ValueError(f'GHCR token request failed (HTTP {auth_error.code})') from None
        raise ValueError('GHCR authentication failed')


def fetch_test(repository, selector, next_version, output, client=None):
    b.require(b.matches(b.REPOSITORY, repository), 'Expected GHCR repository')
    b.require(b.matches(b.VERSION, next_version), 'Expected next MAJOR.MINOR.PATCH')
    b.require(selector == 'test' or b.matches(re.escape(next_version)+r'-test\.(0|[1-9][0-9]*)', selector),
              'Only test or an exact test candidate on the declared next version is accepted')
    output = Path(output)
    # Reserve exclusively before writing. Failed downloads remain inspectable without a selection receipt.
    output.mkdir(mode=0o700)
    blobs = output/'blobs/sha256'
    blobs.mkdir(parents=True)
    client = client or GHCR(repository)
    b.require(client.repository == repository, 'Registry client repository mismatch')
    raw, headers = client.get('manifests', selector, b.MAX_METADATA)
    digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
    b.require(headers.get('Docker-Content-Digest') == digest, 'Registry manifest digest header mismatch')
    manifest = b.document(raw)
    b.keys(manifest, ('schemaVersion','mediaType','artifactType','config','layers'), 'OCI manifest')
    b.require(type(manifest['schemaVersion']) is int and manifest['schemaVersion'] == 2
              and manifest['mediaType'] == b.OCI and manifest['artifactType'] == b.BUNDLE, 'Not a deployment bundle')
    b.descriptor(manifest['config'], b.EMPTY)
    layers = manifest['layers']
    b.require(isinstance(layers,list) and len(layers) == 3, 'Expected three bundle layers')
    types = []
    for layer in layers:
        b.require(isinstance(layer,dict) and layer.get('mediaType') in (b.BUNDLE,*b.ASSETS.values()), 'Unexpected layer')
        b.descriptor(layer,layer['mediaType']); types.append(layer['mediaType'])
    b.require(len(set(types)) == 3 and sum(x['size'] for x in layers) <= b.MAX_TOTAL, 'Invalid layer inventory or total size')
    (blobs/digest.split(':')[1]).write_bytes(raw)
    for item in [manifest['config'],*layers]:
        limit = b.MAX_METADATA if item['mediaType'] in (b.EMPTY,b.BUNDLE) else b.MAX_TOTAL
        b.require(item['size'] <= limit, 'Metadata size exceeds limit')
        data, _ = client.get('blobs', item['digest'], item['size'])
        b.require(len(data) == item['size'] and 'sha256:'+hashlib.sha256(data).hexdigest() == item['digest'], 'Downloaded blob size or digest mismatch')
        target = blobs/item['digest'].split(':')[1]
        if target.exists():
            b.require(target.read_bytes() == data, 'Conflicting blob')
        else:
            target.write_bytes(data)
    reference = repository+'@'+digest
    lock = b.verify(output, reference)
    b.require(lock['candidate'].startswith(next_version+'-test.'), 'Resolved test belongs to another next version')
    b.require(selector == 'test' or selector == lock['candidate'], 'Selected test tag and bundle candidate differ')
    (output/'deployment.lock.json').write_text(json.dumps(lock,sort_keys=True,indent=2)+'\n')
    (output/'oci-layout').write_text('{"imageLayoutVersion":"1.0.0"}\n')
    # Written last: consumers refuse partial downloads. This is transport identity, not production admission.
    receipt = {'schema':1,'channel':'test','selector':selector,'next':next_version,
               'bundle_reference':reference,'candidate':lock['candidate']}
    (output/'selection.json').write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
    return receipt
