#!/usr/bin/env python3
"""Generate deployment manifests without accessing a registry or cluster."""
import argparse
import json
from pathlib import Path
import re

NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
IMAGE = re.compile(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}\Z")


def manifests(name, image):
    if not NAME.fullmatch(name):
        raise ValueError("name must be a lowercase DNS label of 1 to 63 characters")
    if not IMAGE.fullmatch(image):
        raise ValueError("image must be an image reference with a lowercase SHA-256 digest")
    labels = {"app.kubernetes.io/name": name}
    compose = {"name": name, "services": {name: {
        "image": image, "user": "65532:65532", "read_only": True,
        "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
        "tmpfs": ["/tmp:rw,noexec,nosuid,size=64m,mode=1777"],
        "ports": ["127.0.0.1:8080:8080"], "init": True,
        "cpus": 1.0, "mem_limit": "512m", "pids_limit": 256,
    }}}
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
    return {"compose.yaml": compose,
            "kubernetes.json": {"apiVersion": "v1", "kind": "List", "items": items}}


def generate(name, image, output):
    data = manifests(name, image)
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
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = generate(args.name, args.image, args.output)
    except (ValueError, OSError) as error:
        parser.exit(2, f"error: {error}\n")
    print(result)


if __name__ == "__main__":
    main()
