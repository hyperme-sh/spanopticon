#!/usr/bin/env python3
"""Generate deployment manifests without accessing a registry or cluster."""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from assurance import load, settings
from service_channels import reference

NAME = re.compile(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
IMAGE = re.compile(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}\Z")


def manifests(name, image, assurance=None, *, repository="ghcr.io/hyperme-sh/spanopticon/runtime"):
    reference(image, repository)
    result = _manifests(name, image, assurance)
    result["compose.yaml"]["services"][name]["pull_policy"] = "always"
    result["kubernetes.json"]["items"][1]["spec"]["template"]["spec"]["containers"][0]["imagePullPolicy"] = "Always"
    return result


def artifact_manifests(name, image, assurance=None):
    """Historical bundle qualification only; not the service configuration CLI."""
    if not isinstance(image, str) or not IMAGE.fullmatch(image):
        raise ValueError("artifact image requires a lowercase SHA-256 digest")
    return _manifests(name, image, assurance)


def _manifests(name, image, assurance=None):
    environment, secrets = settings(assurance)
    if not NAME.fullmatch(name):
        raise ValueError("name must be a lowercase DNS label of 1 to 63 characters starting with a letter")
    labels = {"app.kubernetes.io/name": name}
    compose = {"name": name, "services": {name: {
        "environment": environment, "secrets": list(secrets),
        "image": image, "user": "65532:65532", "read_only": True,
        "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
        "tmpfs": ["/tmp:rw,noexec,nosuid,size=64m,mode=1777"],
        "ports": ["127.0.0.1:8080:8080"], "init": True,
        "cpus": 1.0, "mem_limit": "512m", "pids_limit": 256,
    }}}
    compose["secrets"] = {name: {"file": "${SPANOPTICON_SECRET_" +
        name.upper().replace("-", "_") + "_FILE:?provide credential file path}"} for name in secrets}
    items = [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": name}},
        {"apiVersion": "apps/v1", "kind": "Deployment",
         "metadata": {"name": name, "namespace": name}, "spec": {
             "replicas": 1, "selector": {"matchLabels": labels},
             "template": {"metadata": {"labels": labels}, "spec": {
                 "automountServiceAccountToken": False,
                 "securityContext": {"runAsNonRoot": True, "runAsUser": 65532,
                                     "runAsGroup": 65532, "fsGroup": 65532,
                                     "seccompProfile": {"type": "RuntimeDefault"}},
                 "containers": [{"name": name, "image": image,
                     "ports": [{"name": "http", "containerPort": 8080}],
                     "securityContext": {"allowPrivilegeEscalation": False,
                         "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}},
                     "resources": {"requests": {"cpu": "100m", "memory": "128Mi"},
                                   "limits": {"cpu": "1", "memory": "512Mi"}},
                     "readinessProbe": {"httpGet": {"path": "/readyz", "port": "http"},
                                        "periodSeconds": 5},
                     "livenessProbe": {"httpGet": {"path": "/healthz", "port": "http"},
                                       "initialDelaySeconds": 10, "periodSeconds": 10},
                     "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}]}],
                 "volumes": [{"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "64Mi"}}]
             }}}},
        {"apiVersion": "v1", "kind": "Service", "metadata": {"name": name, "namespace": name},
         "spec": {"type": "ClusterIP", "selector": labels,
                  "ports": [{"name": "http", "port": 8080, "targetPort": "http"}]}},
        {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
         "metadata": {"name": name + "-default-deny", "namespace": name},
         "spec": {"podSelector": {"matchLabels": labels}, "policyTypes": ["Ingress", "Egress"]}},
    ]
    pod = items[1]["spec"]["template"]["spec"]
    container = pod["containers"][0]
    container["env"] = [{"name": key, "value": value} for key, value in environment.items()]
    for secret in secrets:
        volume = "assurance-" + str(len(pod["volumes"]))
        pod["volumes"].append({"name": volume, "secret": {"secretName": secret,
            "optional": False, "items": [{"key": "token", "path": secret}], "defaultMode": 288}})
        container["volumeMounts"].append({"name": volume, "mountPath": "/run/secrets/" + secret,
            "subPath": secret, "readOnly": True})
    return {"compose.yaml": compose,
            "kubernetes.json": {"apiVersion": "v1", "kind": "List", "items": items}}


def generate(name, image, output, assurance=None, *, repository="ghcr.io/hyperme-sh/spanopticon/runtime"):
    data = manifests(name, image, assurance, repository=repository)
    output = Path(output).absolute()
    # mkdir is an exclusive reservation: refuse directories, files and symlinks.
    # Do not replace another operator's path, even an empty directory.
    output.mkdir()
    # Preserve partial files on I/O failure for operator inspection.
    for filename, content in data.items():
        with (output / filename).open("x", encoding="utf-8") as stream:
            json.dump(content, stream, indent=2)
            stream.write("\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("--image", required=True)
    parser.add_argument("--repository", required=True, help="Approved registry/repository from deployment configuration")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assurance-config", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = generate(args.name, args.image, args.output, load(args.assurance_config), repository=args.repository)
    except (ValueError, OSError) as error:
        parser.exit(2, f"error: {error}\n")
    print(result)


if __name__ == "__main__":
    main()
