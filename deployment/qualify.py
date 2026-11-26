"""Qualify reviewed fleet assets through both deployment consumers before publication."""
import json
from html.parser import HTMLParser
import os
import secrets as random_secrets
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from deployment import bundle as b
from deployment.publish import REPOSITORY, QUALIFIER, admitted
from deployment.runtime import deploy, selection, assets, compose_check, helm_check, command, assurance_settings
from deployment.fleet_inventory import PORTAL_IMAGE, PORTAL_AUTH_NAME, PORTAL_SETTINGS_NAME, FLEET_PULL_SECRET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from assemble_test_bundle import assemble
PORTAL_FOOTER = (
    'fetch("http://127.0.0.1:8080/signed-out",'
    '{redirect:"error",signal:AbortSignal.timeout(8000)})'
    '.then(response=>{if(!response.ok)throw Error("Portal did not serve signed-out HTML");'
    'return response.text()}).then(html=>process.stdout.write(html))'
    '.catch(()=>{process.exitCode=1})'
)


class FooterParagraphs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.footers = []
        self.footer = None
        self.paragraph = None

    def handle_starttag(self, tag, attrs):
        if tag == 'footer':
            self.footer = []
        elif tag == 'p' and self.footer is not None:
            self.paragraph = []

    def handle_data(self, data):
        if self.paragraph is not None:
            self.paragraph.append(data)

    def handle_endtag(self, tag):
        if tag == 'p' and self.paragraph is not None:
            self.footer.append(' '.join(''.join(self.paragraph).split()))
            self.paragraph = None
        elif tag == 'footer' and self.footer is not None:
            self.footers.append(self.footer)
            self.footer = self.paragraph = None


def check_portal_footer(html, candidate, component):
    parser = FooterParagraphs()
    parser.feed(html.decode('utf-8'))
    parser.close()
    release = f"Version {component['version']} · Build {component['source_revision'][:12]}"
    bundle = f'Spanopticon bundle {candidate}'
    b.require(any(release in footer
                  and [label for label in footer if label.startswith('Spanopticon bundle ')] == [bundle]
                  for footer in parser.footers),
              'Portal did not render its qualified release and exact bundle tag in the same footer')


def probe_portal(execute, command_prefix, candidate, component):
    if component is not None:
        check_portal_footer(execute([*command_prefix, 'node', '-e', PORTAL_FOOTER]), candidate, component)


def read(path):
    with Path(path).open('rb') as stream:raw=stream.read(b.MAX_METADATA+1)
    b.require(len(raw)<=b.MAX_METADATA,'Fleet metadata exceeds size limit')
    return b.document(raw)


def inputs(root):
    root=Path(root)
    b.require(not root.is_symlink() and not (root/'test.json').is_symlink(),'Symlinked fleet inputs are unsupported')
    declaration=read(root/'test.json')
    b.keys(declaration,('schema','components','assurance'),'fleet inputs')
    b.require(type(declaration['schema']) is int and declaration['schema']==1,'Unsupported fleet schema')
    assurance_settings(declaration['components'],declaration['assurance'])
    for name in ('compose.json','chart.tgz'):
        path=root/name
        b.require(path.is_file() and not path.is_symlink(),'Missing regular fleet deployment asset')
    return declaration


def assemble_input(root,output,env):
    declaration=inputs(root)
    revision,run=env.get('GITHUB_SHA'),env.get('GITHUB_RUN_ID')
    b.require(b.matches(r'[0-9a-f]{40}',revision) and b.matches(r'[1-9][0-9]*',run),'Expected source/run identity')
    metadata={'schema':1,'candidate':'0.24.0-test.'+run,'source_revision':revision,'components':declaration['components']}
    reference=assemble(metadata,Path(root)/'compose.json',Path(root)/'chart.tgz',REPOSITORY,output)
    receipt={'schema':1,'channel':'test','selector':metadata['candidate'],'next':'0.24.0',
             'bundle_reference':reference,'candidate':metadata['candidate']}
    (Path(output)/'selection.json').write_text(json.dumps(receipt)+'\n')
    layout,_,lock=selection(output)
    portal=lock['components'].get('portal')
    if portal is not None:
        b.require(portal['image'].split('@',1)[0] == PORTAL_IMAGE, 'Unexpected Portal owner image repository')
    payload=assets(layout,lock)
    compose_check(b.document(payload['compose']),lock,declaration['assurance'])
    # Rendering is validated without applying or creating credentials in PR runs.
    rendered=command(['helm','template','fleet-validation',str(Path(root).resolve()/'chart.tgz'),
                      '--namespace','fleet-validation'])
    helm_check(rendered,lock,declaration['assurance'],'fleet-validation')
    return declaration,reference


def native(args):
    return command(args,timeout=240)


def portal_qualification_inputs(config, scratch):
    """Prepare credentials scoped to the disposable qualification containers."""
    b.require(config and Path(config).is_absolute(), "Portal needs a job-local registry config")
    registry = Path(config) / "config.json"
    b.require(registry.is_file() and not registry.is_symlink(), "Portal registry auth is unavailable")
    auth = read(registry).get("auths")
    b.require(isinstance(auth, dict) and set(auth) == {"ghcr.io"}
              and isinstance(auth["ghcr.io"].get("auth"), str) and auth["ghcr.io"]["auth"],
              "Use a job-local config with a single GHCR pull identity")
    secret = scratch / PORTAL_AUTH_NAME
    secret.write_text(random_secrets.token_urlsafe(32))
    secret.chmod(0o444)
    tenant = "00000000-0000-0000-0000-000000000001"
    settings = {
        "AUTH_URL": "https://portal.example.invalid",
        "AUTH_KEYCLOAK_ID": "isolated-portal",
        "AUTH_KEYCLOAK_ISSUER": "https://auth.example.invalid/realms/portal",
        "NEXT_PUBLIC_CPEDGE_URL": "https://edge.example.invalid",
        "NEXT_PUBLIC_LOGIN_TENANT_ID": tenant,
        "NEXT_PUBLIC_AUTH_SCOPES_REQUIRED": "true",
        "NEXT_PUBLIC_TENANT_PICKER": "false",
        "PORTAL_TENANT_ROUTES": "{}",
        "PORTAL_TENANT_VERSIONS": json.dumps({tenant: "1.2.3"}),
    }
    return registry, secret, settings


def qualify(root,output,env=None,*,docker_context='default',kube_context=None,apply=deploy,execute=native):
    env=dict(os.environ) if env is None else env
    b.require(env.get('GITHUB_REPOSITORY')=='hyperme-sh/spanopticon'
              and env.get('GITHUB_EVENT_NAME') in ('push', 'schedule') and env.get('GITHUB_REF')=='refs/heads/main'
              and env.get('GITHUB_WORKFLOW_REF')==QUALIFIER
              and b.matches(r'[1-9][0-9]*',env.get('GITHUB_RUN_ATTEMPT')),
              'Only the main fleet qualification workflow may apply')
    b.require(kube_context and kube_context.startswith('kind-spanopticon-fleet-'),
              'Qualification requires a disposable kind-spanopticon-fleet-* context')
    b.require(docker_context=='default','Qualification uses the disposable job Docker daemon')
    output=Path(output);output.mkdir(mode=0o700)
    declaration,reference=assemble_input(root,output/'oci',env)
    portal=declaration['components'].get('portal')
    assurance=declaration['assurance'];_,secret_names=assurance_settings(declaration['components'],assurance)
    try:credentials=b.document(env.get('FLEET_TEST_CREDENTIALS','{}')) if assurance is not None else {}
    except (ValueError,TypeError):raise ValueError('Invalid fleet qualification credentials') from None
    b.require(set(credentials)==set(secret_names) and all(isinstance(v,str) and 0<len(v)<=65536 for v in credentials.values()),
              'Supply exactly the selected assurance credentials')
    name='sp-fleet-'+uuid.uuid4().hex[:12]
    receipts={};created=False;compose_attempted=False;saved={}
    with tempfile.TemporaryDirectory(prefix='fleet-qualification-') as directory:
        scratch=Path(directory)
        compose_command=['docker','--context',docker_context,'compose','--env-file',os.devnull,'-p',name,
                         '-f',str(scratch/'compose/compose.resolved.json')]
        helm_portal=['kubectl','--context',kube_context,'-n',name,'exec','deployment/portal','--']
        portal_inputs=portal_qualification_inputs(env.get('DOCKER_CONFIG'),scratch) if assurance is None else None
        try:
            execute(['kubectl','--context',kube_context,'create','namespace',name]);created=True
            if portal_inputs:
                registry, auth_secret, portal_settings = portal_inputs
                kube = ['kubectl','--context',kube_context,'-n',name,'create']
                execute([*kube,'secret','generic',FLEET_PULL_SECRET,
                         '--type=kubernetes.io/dockerconfigjson','--from-file=.dockerconfigjson='+str(registry)])
                execute([*kube,'secret','generic',PORTAL_AUTH_NAME,'--from-file=token='+str(auth_secret)])
                execute([*kube,'configmap',PORTAL_SETTINGS_NAME,
                         *['--from-literal='+key+'='+value for key,value in portal_settings.items()]])
                for key,value in {**portal_settings, 'PORTAL_AUTH_SECRET_FILE': str(auth_secret)}.items():
                    saved[key]=os.environ.get(key)
                    os.environ[key]=value
            for secret,value in credentials.items():
                path=scratch/secret;path.write_text(value);path.chmod(0o444)
                variable='SPANOPTICON_SECRET_'+secret.upper().replace('-','_')+'_FILE'
                saved[variable]=os.environ.get(variable);os.environ[variable]=str(path)
                execute(['kubectl','--context',kube_context,'-n',name,'create','secret','generic',secret,'--from-file=token='+str(path)])
            compose_attempted=True
            receipts['compose']=apply(output/'oci','compose',name,assurance,scratch/'compose',apply=True,context=docker_context)
            candidate=receipts['compose']['candidate']
            probe_portal(execute,[*compose_command,'exec','-T','portal'],candidate,portal)
            receipts['helm']=apply(output/'oci','helm',name,assurance,scratch/'helm',apply=True,context=kube_context)
            # Exercise an upgrade with the same immutable assets as well as first install.
            probe_portal(execute,helm_portal,candidate,portal)
            apply(output/'oci','helm',name,assurance,scratch/'upgrade',apply=True,context=kube_context)
            probe_portal(execute,helm_portal,candidate,portal)
        finally:
            try:
                if compose_attempted and (scratch/'compose/compose.resolved.json').exists():
                    execute([*compose_command,'down'])
            finally:
                try:
                    if created:execute(['kubectl','--context',kube_context,'delete','namespace',name,'--wait=true','--timeout=90s'])
                finally:
                    for variable,old in saved.items():
                        if old is None:os.environ.pop(variable,None)
                        else:os.environ[variable]=old
    record={'schema':'spanopticon-fleet-qualification/v1','source_revision':env['GITHUB_SHA'],
            'run_id':env['GITHUB_RUN_ID'],'run_attempt':env['GITHUB_RUN_ATTEMPT'],
            'bundle_reference':reference,'receipts':receipts}
    # Reserve no success record until both engines, upgrade and cleanup succeed.
    path=output/'qualification.json';path.write_text(json.dumps(record,sort_keys=True,indent=2)+'\n')
    try:admitted(output,env)
    except Exception:
        path.unlink();raise
    return record
