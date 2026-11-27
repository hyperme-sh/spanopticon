"""Verify a Gateway OCI successor against its published runtime."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from component_release import candidate_component
from initial_oci_release import IMAGE_REPOSITORY, OWNER, git, verify_candidate_image
from release.accepted import SHA, github_json, verify_accepted_record
from release.policy import PolicyError, contract_path, load_bundle, require, same_json, version


COMPONENT = 'hypermesh-gateway'
OPENAPI = ('import json, sys; from hypermesh_gateway_service.app import app; '
           'json.dump(app.openapi(), sys.stdout, sort_keys=True)')


def read_openapi(image):
    require(re.fullmatch(re.escape(IMAGE_REPOSITORY) + r'@sha256:[0-9a-f]{64}', image),
            'Expected an immutable Gateway OCI image')
    try:
        subprocess.run(['docker', 'pull', '--quiet', '--platform', 'linux/amd64', image],
                       capture_output=True, check=True, timeout=90)
        result = subprocess.run(
            ['docker', 'run', '--rm', '--pull=never', '--platform', 'linux/amd64', '--network=none',
             '--cap-drop=ALL', '--security-opt=no-new-privileges', '--read-only', '--tmpfs',
             '/tmp:rw,nosuid,nodev,noexec,size=16m', '--entrypoint', '/app/.venv/bin/python',
             image, '-I', '-B', '-c', OPENAPI],
            check=True, capture_output=True, timeout=30,
        )
        require(len(result.stdout) <= 1024 * 1024, 'Gateway OpenAPI export exceeds the size limit')
        return json.loads(result.stdout)
    except (subprocess.SubprocessError, OSError, ValueError):
        raise PolicyError('Unable to read the qualified Gateway runtime OpenAPI') from None


def compare_api(before, after, previous, candidate):
    for document, expected in ((before, previous), (after, candidate)):
        require(isinstance(document, dict) and document.get('openapi') == '3.1.0'
                and isinstance(document.get('paths'), dict) and bool(document['paths'])
                and isinstance(document.get('info'), dict) and document['info'].get('version') == expected,
                'Gateway runtime API or its reported version differs from its release')
    def without_version(document):
        return {**document, 'info': {key: value for key, value in document['info'].items() if key != 'version'}}
    require(same_json(without_version(before), without_version(after)),
            'Gateway OCI successor changed the published OpenAPI contract')


def require_successor(previous, candidate):
    major, minor, patch = version(previous)
    allowed = {(major, minor, patch + 1), (major, minor + 1, 0)}
    require(version(candidate) in allowed,
            'Gateway OCI successor must be the next patch or minor from its accepted baseline')


def admit(current, baseline, baseline_revision, repository, line, requested, revision, ref,
          *, fetch=github_json, export=read_openapi):
    require(repository == OWNER and requested and SHA.fullmatch(revision) and SHA.fullmatch(baseline_revision),
            'Expected the Gateway owner and exact release and baseline revisions')
    current, baseline = Path(current).resolve(), Path(baseline).resolve()
    require(git(baseline, 'rev-parse', 'HEAD').decode().strip() == baseline_revision,
            'Accepted checkout differs from its configured revision')
    git(current, 'merge-base', '--is-ancestor', baseline_revision, revision)
    previous = verify_accepted_record(baseline, repository, required=True, fetch=fetch)
    accepted, _ = load_bundle(contract_path(baseline, 'releases/accepted/release.json'))
    old = accepted['components'][COMPONENT]
    prior_artifact = old['artifact']
    require(prior_artifact['kind'] == 'container'
            and prior_artifact['uri'] == f"{IMAGE_REPOSITORY}@sha256:{prior_artifact['sha256']}",
            'The accepted baseline must be the immutable Gateway OCI runtime')
    candidate = candidate_component(current, repository, line, requested)
    require_successor(previous, candidate['version'])
    tag, image, source = verify_candidate_image(current, candidate, revision, ref, fetch, previous=previous)
    compare_api(export(prior_artifact['uri']), export(image), previous, candidate['version'])
    return tag, image, source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ('repository-root', 'baseline-root', 'baseline-revision', 'repository', 'line',
                     'requested-version', 'revision', 'ref'):
        parser.add_argument(f'--{argument}', required=True)
    args = parser.parse_args()
    try:
        tag, image, source = admit(args.repository_root, args.baseline_root, args.baseline_revision,
                                   args.repository, args.line, args.requested_version, args.revision, args.ref)
    except (PolicyError, OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        parser.exit(1, f'Gateway OCI successor admission rejected: {error}\n')
    if output := os.environ.get('GITHUB_OUTPUT'):
        with open(output, 'a', encoding='utf-8') as stream:
            stream.write(f'image={image}\nsource-revision={source}\n')
    print(f'{tag}: {image} qualified at {source} with the published Gateway API preserved')


if __name__ == '__main__':
    main()
