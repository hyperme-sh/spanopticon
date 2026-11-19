"""Render disabled production Payment owner contracts for Compose and Helm."""
import argparse
import json
from pathlib import Path
import re

from deployment.generate import chart_bytes

FLAGS = (
    'PAYMENT_USD_CHECKOUT_ENABLED', 'PAYMENT_USD_RECONCILIATION_ENABLED',
    'PAYMENT_USD_CHECKOUT_LIVEMODE', 'PAYMENT_CONNECT_ENABLED',
    'PAYMENT_CONNECT_TRANSFERS_ENABLED', 'PAYMENT_CONNECT_LIVE_TRANSFERS_ENABLED',
    'PAYMENT_CONNECT_LIVE_BANK_PAYOUTS_ENABLED', 'PAYMENT_CONNECT_EMBEDDED_ENABLED',
    'PAYMENT_USD_LEASE_QUOTES_ENABLED',
)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def name(value):
    require(isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9-]{0,62}', value),
            'Invalid resource name')
    return value


def literal(value):
    require(isinstance(value, str) and '\x00' not in value, 'Expected string setting')
    # Compose interpolation must not reinterpret data as a credential variable.
    return value.replace('$', '$$')


def render(plan):
    require(plan.get('schema_version') == 1 and plan.get('active') is False,
            'Expected inactive owner plan')
    apps = plan['apps']
    require({'payment', 'plane', 'user'} <= apps.keys(), 'Payment, Plane and User required')
    relays = plan['payment_events']['relays']
    require(set(relays) == {'payment-paid-relay', 'payment-archive-relay'}, 'Both relays required')
    roles = {key: apps[key] for key in ('payment', 'plane', 'user')}
    roles['payment-worker'] = plan['workers']['payment']
    roles.update(relays)
    for key in ('payment', 'payment-worker'):
        require(all(roles[key]['env'].get(flag) == {'value': 'false'} for flag in FLAGS),
                'Production billing must remain disabled')
    require(plan.get('payment_portal_env', {}).get('NEXT_PUBLIC_HYPERMESH_CHECKOUT_BUYER_USE') == 'false',
            'Portal billing must remain disabled')
    compose = {'services': {}, 'secrets': {}}
    items, bindings = [], {}
    for role, spec in roles.items():
        name(role)
        image = spec['image']
        require(isinstance(image, str) and re.fullmatch(r'[a-z0-9./_-]+(?::[A-Za-z0-9_.-]+|@sha256:[a-f0-9]{64})', image),
                'Expected explicit owner image')
        secret_names = {key: name(role + '-' + key) for key in spec['secrets']}
        env, kube_env, mounts, volumes, compose_secrets = {}, [], [], [], []
        for key, ref in spec['secrets'].items():
            alias = secret_names[key]
            bindings[alias] = {'key_vault_secret_id': ref['key_vault_secret_id'],
                               'kubernetes_secret': alias, 'kubernetes_key': 'value'}
        for key, value in spec['env'].items():
            require(re.fullmatch(r'[A-Z][A-Z0-9_]*', key), 'Invalid environment key')
            if set(value) == {'value'}:
                env[key] = literal(value['value'])
                kube_env.append({'name': key, 'value': value['value']})
            else:
                require(set(value) == {'secret_name'} and value['secret_name'] in secret_names,
                        'Unknown secret environment binding')
                alias = secret_names[value['secret_name']]
                variable = 'SPANOPTICON_' + alias.upper().replace('-', '_')
                env[key] = '${' + variable + ':?provide the declared production secret}'
                bindings[alias]['compose_value_variable'] = variable
                kube_env.append({'name': key, 'valueFrom': {'secretKeyRef': {'name': alias, 'key': 'value'}}})
        for key, mount in spec.get('secret_files', {}).items():
            require(key in secret_names, 'Unknown secret file binding')
            alias = secret_names[key]
            target = mount['mount_path'] + '/' + mount['filename']
            require(target.startswith('/run/') and '..' not in Path(target).parts
                    and re.fullmatch(r'/[A-Za-z0-9/_.-]+', target), 'Invalid secret mount')
            variable = 'SPANOPTICON_' + alias.upper().replace('-', '_') + '_FILE'
            compose['secrets'][alias] = {'file': '${' + variable + ':?provide the declared production secret file}'}
            bindings[alias]['compose_file_variable'] = variable
            compose_secrets.append({'source': alias, 'target': target, 'mode': 0o440})
            mounts.append({'name': alias, 'mountPath': mount['mount_path'], 'readOnly': True})
            volumes.append({'name': alias, 'secret': {'secretName': alias, 'defaultMode': 0o440,
                            'items': [{'key': 'value', 'path': mount['filename']}]}})
        service = {'image': image, 'restart': 'unless-stopped', 'profiles': ['production-payment'],
                   'environment': env, 'read_only': True, 'cap_drop': ['ALL'],
                   'security_opt': ['no-new-privileges:true'], 'user': '65532:65532',
                   'tmpfs': ['/tmp:rw,nosuid,noexec,size=33554432'], 'secrets': compose_secrets}
        container = {'name': role, 'image': image, 'env': kube_env, 'volumeMounts': mounts,
                     'securityContext': {'runAsNonRoot': True, 'runAsUser': 65532, 'runAsGroup': 65532,
                                         'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False,
                                         'capabilities': {'drop': ['ALL']}}}
        if 'command' in spec:
            require(isinstance(spec['command'], list) and all(isinstance(x, str) for x in spec['command']), 'Invalid command')
            service['entrypoint'] = [literal(x) for x in spec['command']]
            service['command'] = [literal(x) for x in spec.get('args', [])]
            container['command'] = spec['command']
            container['args'] = spec.get('args', [])
        port = spec.get('probe_port')
        path = spec.get('probe_path') or spec.get('probes', {}).get('readiness')
        if port and path:
            container['readinessProbe'] = {'httpGet': {'path': path, 'port': port}, 'periodSeconds': 10}
            container['livenessProbe'] = {'httpGet': {'path': spec.get('probes', {}).get('liveness', path), 'port': port},
                                          'initialDelaySeconds': 30, 'periodSeconds': 30}
            check = 'import urllib.request; urllib.request.urlopen(' + repr(f'http://127.0.0.1:{port}{path}') + ', timeout=3)'
            service['healthcheck'] = {'test': ['CMD', '/app/.venv/bin/python', '-c', check],
                                      'interval': '30s', 'timeout': '5s', 'retries': 3, 'start_period': '30s'}
        cpu, memory = str(spec['cpu']), spec['memory']
        container['resources'] = {'requests': {'cpu': cpu, 'memory': memory}, 'limits': {'cpu': cpu, 'memory': memory}}
        service['cpus'] = cpu
        # Kubernetes accepts fractional Gi; Compose accepts bytes.
        require(re.fullmatch(r'[0-9]+(?:\.[0-9]+)?Gi', memory), 'Expected Gi memory')
        service['mem_limit'] = int(float(memory[:-2]) * 1024**3)
        compose['services'][role] = service
        container['volumeMounts'].append({'name': 'tmp', 'mountPath': '/tmp'})
        volumes.append({'name': 'tmp', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '32Mi'}})
        items.append({'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': role},
                      'spec': {'replicas': spec.get('replicas', spec.get('min_replicas', 1)),
                               'selector': {'matchLabels': {'app': role}},
                               'template': {'metadata': {'labels': {'app': role}},
                                            'spec': {'automountServiceAccountToken': False,
                                                     'securityContext': {'fsGroup': 65532, 'seccompProfile': {'type': 'RuntimeDefault'}},
                                                     'containers': [container], 'volumes': volumes}}}})
        if 'port' in spec:
            items.append({'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': role},
                          'spec': {'type': 'ClusterIP', 'selector': {'app': role},
                                   'ports': [{'port': spec['port'], 'targetPort': spec['port']}]}})
    return compose, items, {'secrets': bindings, 'receiver_maps': plan['payment_events']['receiver_maps'],
                            'portal_env': plan['payment_portal_env']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--chart-version', required=True)
    args = parser.parse_args()
    require(re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?', args.chart_version), 'Expected chart metadata version')
    compose, items, bindings = render(json.loads(args.plan.read_text()))
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    (args.output / 'compose.yaml').write_text(json.dumps(compose, indent=2) + '\n')
    (args.output / 'chart.tgz').write_bytes(chart_bytes(args.chart_version, items))
    (args.output / 'bindings.json').write_text(json.dumps(bindings, indent=2) + '\n')


if __name__ == '__main__':
    main()
