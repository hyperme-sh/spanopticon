"""Synthetic HTTP adapter fixture; does not qualify an assurance provider."""
import io
import json
from pathlib import Path
import sys
import tarfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from new_plane import manifests
from deployment import bundle as b
from test_deployment_bundle import BundleFixture,encode

ASSURANCE={'mode':'verification','providers':{'verification':{'url':'https://verification.example.invalid',
             'credential_secret':'verification-test-client'}}}
SERVER='from http.server import BaseHTTPRequestHandler,HTTPServer\nclass H(BaseHTTPRequestHandler):\n def do_GET(self):\n  self.send_response(200);self.end_headers();self.wfile.write(b"ok")\n def log_message(self,*args): pass\nHTTPServer(("0.0.0.0",8080),H).serve_forever()'
HEALTH=['CMD','python3','-c','import urllib.request; urllib.request.urlopen("http://127.0.0.1:8080/readyz", timeout=2).read()']


def runtime_fixture(root,image=None):
    fixture=BundleFixture(root)
    image=image or fixture.bundle['components']['api']['image']
    fixture.bundle['components']['api']['image']=image
    docs=manifests('api',image,ASSURANCE)
    compose=docs['compose.yaml'];compose.pop('name')
    service=compose['services'].pop('api');compose['services']['api']=service
    service['command']=['python3','-c',SERVER]
    service['healthcheck']={'test':HEALTH,'interval':'2s','timeout':'3s','retries':15,'start_period':'2s'}
    service['restart']='unless-stopped';service.pop('ports')
    items=[i for i in docs['kubernetes.json']['items'] if i['kind']!='Namespace']
    for item in items:item['metadata']['namespace']='__SPANOPTICON_NAMESPACE__'
    deployment=next(i for i in items if i['kind']=='Deployment')
    deployment['spec']['template']['spec']['containers'][0]['command']=['python3','-c',SERVER]
    # Only the disposable fixture permits DNS and calls to its own test adapter.
    # The generated default-deny policy and real deployment assets are unchanged.
    items.append({'apiVersion':'networking.k8s.io/v1','kind':'NetworkPolicy',
        'metadata':{'name':'api-test-connectivity','namespace':'__SPANOPTICON_NAMESPACE__'},
        'spec':{'podSelector':{'matchLabels':{'app.kubernetes.io/name':'api'}},
                'policyTypes':['Ingress','Egress'],
                'ingress':[{'from':[{'podSelector':{'matchLabels':{'app.kubernetes.io/name':'api'}}}]}],
                'egress':[{'to':[{'podSelector':{'matchLabels':{'app.kubernetes.io/name':'api'}}}]},
                          {'to':[{'namespaceSelector':{'matchLabels':{'kubernetes.io/metadata.name':'kube-system'}},
                                  'podSelector':{'matchLabels':{'k8s-app':'kube-dns'}}}],
                           'ports':[{'protocol':'UDP','port':53},{'protocol':'TCP','port':53}]}]}})
    from deployment.generate import chart_bytes
    chart=chart_bytes(fixture.bundle['candidate'],items)
    fixture.bundle['deployment_assets']={'compose':fixture.put(encode(compose),b.ASSETS['compose']),
                                        'helm':fixture.put(chart,b.ASSETS['helm'])}
    fixture.refresh()
    return fixture,compose,items
