"""Generate an additive configuration overlay for existing SEA transfer owners."""
import argparse
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import uuid

AUTH_PATH = '/auth/sea-device-link/v1/'
ACL_PATH = '/api/v1/acl/sociacl/device-transfer/'


def exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise ValueError('Unexpected configuration fields')


def origin(value):
    if not isinstance(value, str) or not re.fullmatch(r'https://[a-z0-9.-]+(?::[1-9][0-9]{0,4})?', value):
        raise ValueError('Canonical HTTPS origin required')
    parsed = urlsplit(value)
    host = parsed.hostname
    if not host or parsed.port == 443 or parsed.port and parsed.port > 65535:
        raise ValueError('Noncanonical origin')
    if re.fullmatch(r'[0-9]+|0x[0-9a-f]+', host.rsplit('.', 1)[-1]):
        if str(ipaddress.IPv4Address(host)) != host:
            raise ValueError('Noncanonical IP origin')
    elif len(host) > 253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in host.split('.')):
        raise ValueError('Noncanonical DNS origin')
    return value


def private_path(value):
    if not isinstance(value, str) or not value.startswith('/') or len(value) > 1024 or any(c in value for c in ('$','\n','\r','\x00')) or '..' in Path(value).parts:
        raise ValueError('Absolute deployment-owned private path required')
    return value


def mount(source, target):
    return {'type': 'bind', 'source': source, 'target': target, 'read_only': True, 'bind': {'create_host_path': False}}


def compile_config(value):
    exact(value, 'version edgeOrigin clients crossApplicationPairs ownerFiles')
    if type(value['version']) is not int or value['version'] != 1 or value['crossApplicationPairs'] != []:
        raise ValueError('Version 1 supports same-application transfers only')
    edge = origin(value['edgeOrigin'])
    clients = value['clients']
    if not isinstance(clients, list) or not 1 <= len(clients) <= 8:
        raise ValueError('Configure one to eight client scopes')
    files = value['ownerFiles']
    exact(files, 'authEnvironment aclPrivateDirectory s3rchEnvironment')
    for path in files.values(): private_path(path)
    scopes, portal, s3rch, seen = [], [], None, set()
    portal_origin = None
    for client in clients:
        exact(client, 'application origin tenant audience provider')
        app, url, tenant = client['application'], origin(client['origin']), client['tenant']
        if app not in ('hyperme.sh', 's3r.ch') or not isinstance(tenant, str) or str(uuid.UUID(tenant)) != tenant or uuid.UUID(tenant).int == 0:
            raise ValueError('Supported application and canonical nonzero tenant required')
        audience = client['audience']
        if not isinstance(audience, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', audience):
            raise ValueError('Deployment audience required')
        if (app, tenant) in seen:
            raise ValueError('Duplicate application tenant')
        seen.add((app, tenant))
        scope = dict(sourceOrigin=url, destinationOrigin=url, application=app, tenant=tenant, audience=audience)
        scopes.append(scope)
        binding = dict(scope=scope, auth=edge+AUTH_PATH, acl=edge+ACL_PATH)
        if app == 'hyperme.sh':
            if client['provider'] is not None or portal_origin not in (None, url):
                raise ValueError('Portal uses its existing native login and one origin')
            portal_origin = url
            portal.append(binding)
        else:
            if s3rch is not None or not isinstance(client['provider'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', client['provider']):
                raise ValueError('One s3r.ch scope and existing OIDC provider required')
            s3rch = dict(v=1, **binding, provider=client['provider'])
    shared = {'version': 1, 'scopes': scopes}
    services = {
        'auth-service': {'env_file': [files['authEnvironment']], 'environment': {'AUTH_SEA_PAIRING_SCOPES_FILE': '/run/device-transfer/scopes.json'}, 'volumes': [mount('./scopes.json', '/run/device-transfer/scopes.json')]},
        'controlplane-edge': {'environment': {'CPEDGE_DEVICE_TRANSFER_SCOPES_FILE': '/run/device-transfer/scopes.json'}, 'volumes': [mount('./scopes.json', '/run/device-transfer/scopes.json')]},
        'acl-service': {'environment': {'SOCIACL_DEVICE_TRANSFER_BINDINGS_FILE': '/run/device-transfer-private/bindings.json'}, 'volumes': [mount(files['aclPrivateDirectory'], '/run/device-transfer-private')]},
    }
    if portal:
        services['controlplane-portal'] = {'environment': {'PORTAL_DEVICE_TRANSFER_CONFIG': json.dumps(dict(v=1, origin=portal_origin, bindings=portal), separators=(',', ':'))}}
    if s3rch:
        services['s3rch'] = {'env_file': [files['s3rchEnvironment']], 'environment': {'S3RCH_DEVICE_TRANSFER_CONFIG': json.dumps(s3rch, separators=(',', ':'))}}
    # Overlay existing owner services. Do not create databases, people, policies,
    # migrations, secrets, image pins or grants as a side effect of generation.
    return {'compose.override.json': {'services': services}, 'scopes.json': shared,
            'capabilities.json': {'version': 1, 'configured': True, 'ready': False, 'crossApplicationPairs': [],
                'clients': [{'application': s['application'], 'scope': s} for s in scopes]}}


def write(value, output):
    generated = compile_config(value)
    output = Path(output).absolute()
    private_path(str(output))
    for service in generated['compose.override.json']['services'].values():
        for volume in service.get('volumes', []):
            if volume['source'] == './scopes.json':
                volume['source'] = str(output/'scopes.json')
    output.mkdir(mode=0o700)
    for name, body in generated.items():
        with (output/name).open('x') as stream:
            json.dump(body, stream, indent=2)
            stream.write('\n')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        raw = args.config.read_bytes()
        if len(raw) > 32768: raise ValueError('Configuration too large')
        write(json.loads(raw), args.output)
    except (ValueError, OSError):
        parser.exit(2, 'Device transfer configuration refused; check declared inputs and output path.\n')


if __name__ == '__main__': main()
