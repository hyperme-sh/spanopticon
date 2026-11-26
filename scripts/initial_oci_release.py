#!/usr/bin/env python3
"""Admit the first Gateway OCI release from a qualified main build."""

import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess

from component_release import initial_component, require_no_stable_releases
from release.accepted import SHA, github_json
from release.policy import PolicyError, contract_path, json_document, require


OWNER = 'hyperme-sh/gateway'
IMAGE_REPOSITORY = 'ghcr.io/hyperme-sh/hypermesh-gateway/runtime'
BUILD_WORKFLOW = '.github/workflows/package.yml'
RELEASE_FILES = {
    '.github/workflows/release-admission.yml',
    '.github/workflows/release-publish.yml',
    'docs/releasing.md',
    'releases/candidate/contract.json',
    'releases/candidate/oci-source.json',
    'releases/candidate/release.json',
}
STABLE_TAG = re.compile(r'v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)')


def git(root, *arguments):
    try:
        return subprocess.run(
            ['git', '-C', str(root), *arguments],
            check=True, capture_output=True, timeout=10,
        ).stdout
    except subprocess.SubprocessError:
        raise PolicyError('Unable to verify Gateway release checkout') from None


def verify_checkout(root, revision, source, contract_digest, tag, ref):
    require(git(root, 'rev-parse', 'HEAD').decode().strip() == revision,
            'Release checkout does not match the workflow revision')
    require(ref in {'refs/heads/main', f'refs/tags/{tag}'},
            'Expected Gateway main or the requested stable tag')
    if ref.startswith('refs/tags/'):
        require(git(root, 'rev-parse', f'{ref}^{{commit}}').decode().strip() == revision,
                'Requested stable tag points to a different revision')
    tags = git(root, 'tag', '--list', 'v*').decode().splitlines()
    require(not any(STABLE_TAG.fullmatch(name) and f'refs/tags/{name}' != ref for name in tags),
            'Stable tag already exists; an accepted baseline is required')
    git(root, 'merge-base', '--is-ancestor', source, revision)
    changed = git(root, 'diff', '--no-ext-diff', '--no-textconv', '--no-renames',
                  '--name-only', '-z', source, revision, '--').decode().split('\0')
    require(set(filter(None, changed)) <= RELEASE_FILES,
            'Release source differs from the qualified build outside release metadata')
    built_contract = git(root, 'show', f'{source}:releases/prepared/contract.json')
    require(hashlib.sha256(built_contract).hexdigest() == contract_digest,
            'Release contract differs from the qualified build contract')


def verify_build(run_id, attempt, source, digest, fetch):
    path = f'repos/{OWNER}/actions/runs/{run_id}/attempts/{attempt}'
    run = fetch(path)
    require(isinstance(run, dict) and run.get('id') == run_id and run.get('run_attempt') == attempt
            and run.get('repository', {}).get('full_name') == OWNER
            and run.get('head_repository', {}).get('full_name') == OWNER
            and run.get('path') == BUILD_WORKFLOW and run.get('event') == 'push'
            and run.get('head_branch') == 'main' and run.get('head_sha') == source
            and run.get('status') == 'completed' and run.get('conclusion') == 'success',
            'Expected a successful owner main Gateway package build at the declared revision')
    result = fetch(f'{path}/jobs?per_page=100')
    jobs = result.get('jobs')
    require(isinstance(jobs, list) and 0 < len(jobs) <= 100
            and result.get('total_count') == len(jobs) and all(isinstance(job, dict) for job in jobs),
            'Incomplete Gateway build job inventory')
    for name in ('package', 'publish', f'Qualified OCI sha256:{digest}'):
        matches = [job for job in jobs if job.get('name') == name]
        require(len(matches) == 1, 'Gateway build job or qualified OCI digest is missing or ambiguous')
        job = matches[0]
        require(job.get('run_id') == run_id and job.get('run_attempt') == attempt and job.get('head_sha') == source
                and job.get('status') == 'completed' and job.get('conclusion') == 'success',
                'Gateway package, publisher and exact OCI qualification must all succeed')


def admit(root, repository, line, requested, revision, ref, *, fetch=github_json):
    require(repository == OWNER, 'Initial OCI admission is enabled for the Gateway owner only')
    require(requested and SHA.fullmatch(revision), 'A version and immutable release checkout revision are required')
    root = Path(root).resolve()
    component = initial_component(root, repository, line, requested)
    artifact = component['artifact']
    digest = artifact['sha256']
    require(artifact['kind'] == 'container' and artifact['uri'] == f'{IMAGE_REPOSITORY}@sha256:{digest}',
            'First Gateway release requires the immutable owner OCI image')
    require(component['contract'] == 'contract.json', 'Expected the declared Gateway contract path')
    provenance = json_document(contract_path(root, 'releases/candidate/oci-source.json'))
    require(isinstance(provenance, dict) and set(provenance) == {'source_revision', 'run_id', 'run_attempt'},
            'Expected the Gateway OCI build reference')
    source, run_id, attempt = (provenance[name] for name in ('source_revision', 'run_id', 'run_attempt'))
    require(isinstance(source, str) and SHA.fullmatch(source)
            and type(run_id) is int and run_id > 0 and type(attempt) is int and attempt > 0,
            'Invalid Gateway OCI build reference')
    tag = 'v' + component['version']
    verify_checkout(root, revision, source, component['contract_sha256'], tag, ref)
    require_no_stable_releases(repository, lambda path: fetch(path, document_type=list))
    verify_build(run_id, attempt, source, digest, fetch)
    return tag, artifact['uri'], source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ('repository-root', 'repository', 'line', 'requested-version', 'revision', 'ref'):
        parser.add_argument(f'--{argument}', required=True)
    args = parser.parse_args()
    try:
        tag, image, source = admit(args.repository_root, args.repository, args.line, args.requested_version,
                                   args.revision, args.ref)
    except (PolicyError, OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        parser.exit(1, f'OCI release admission rejected: {error}\n')
    if output := os.environ.get('GITHUB_OUTPUT'):
        with open(output, 'a', encoding='utf-8') as stream:
            stream.write(f'image={image}\nsource-revision={source}\n')
    print(f'{tag}: {image} qualified at {source}')


if __name__ == '__main__':
    main()
