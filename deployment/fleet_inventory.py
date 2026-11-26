"""Render fleet assets from enrolled services and exact owner release identities."""
import json
from pathlib import Path
import re

from deployment import bundle as b
from deployment.generate import chart_bytes

PORTAL_IMAGE = "ghcr.io/hyperme-sh/controlplane-portal/runtime"
PORTAL_HEALTH = (
    'fetch("http://127.0.0.1:8080/signed-out",{redirect:"error",'
    'signal:AbortSignal.timeout(3000)}).then(r=>{if(!r.ok) process.exitCode=1})'
    '.catch(()=>{process.exitCode=1})'
)
PORTAL_AUTH_NAME = "portal-auth"
PORTAL_SETTINGS_NAME = "portal-settings"
FLEET_PULL_SECRET = "fleet-ghcr"
PORTAL_RUNTIME_SETTINGS = (
    "AUTH_URL",
    "AUTH_KEYCLOAK_ID",
    "AUTH_KEYCLOAK_ISSUER",
    "NEXT_PUBLIC_CPEDGE_URL",
    "NEXT_PUBLIC_LOGIN_TENANT_ID",
    "NEXT_PUBLIC_AUTH_SCOPES_REQUIRED",
    "NEXT_PUBLIC_TENANT_PICKER",
    "PORTAL_TENANT_ROUTES",
    "PORTAL_TENANT_VERSIONS",
)


def portal_assets(image):
    labels = {"app.kubernetes.io/name": "portal"}
    health = ["node", "-e", PORTAL_HEALTH]
    compose = {
        "image": image,
        "environment": {
            "AUTH_TRUST_HOST": "true",
            **{key: "${" + key + ":?provide Portal runtime configuration}" for key in PORTAL_RUNTIME_SETTINGS},
        },
        "secrets": [PORTAL_AUTH_NAME],
        "entrypoint": [
            "/bin/sh", "-ec",
            'AUTH_SECRET="$$(cat /run/secrets/portal-auth)"; test -n "$$AUTH_SECRET"; '
            'export AUTH_SECRET; exec node server.js',
        ],
        "read_only": True,
        "tmpfs": ["/tmp:rw,noexec,nosuid,size=64m,mode=1777"],
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "healthcheck": {
            "test": ["CMD", *health], "interval": "5s", "timeout": "4s",
            "retries": 12, "start_period": "5s",
        },
    }
    metadata = {"name": "portal", "labels": labels}
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": metadata,
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "automountServiceAccountToken": False,
                    "imagePullSecrets": [{"name": FLEET_PULL_SECRET}],
                    "securityContext": {
                        "runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}}],
                    "containers": [{
                        "name": "portal",
                        "image": image,
                        "ports": [{"name": "http", "containerPort": 8080}],
                        "env": [
                            {"name": "AUTH_TRUST_HOST", "value": "true"},
                            {"name": "AUTH_SECRET", "valueFrom": {
                                "secretKeyRef": {"name": PORTAL_AUTH_NAME, "key": "token"},
                            }},
                            *[{"name": key, "valueFrom": {
                                "configMapKeyRef": {"name": PORTAL_SETTINGS_NAME, "key": key},
                            }} for key in PORTAL_RUNTIME_SETTINGS],
                        ],
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]},
                        },
                        "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
                        "resources": {
                            "requests": {"cpu": "100m", "memory": "128Mi"},
                            "limits": {"cpu": "1", "memory": "512Mi"},
                        },
                        "readinessProbe": {"exec": {"command": health}, "periodSeconds": 5, "timeoutSeconds": 4},
                        "livenessProbe": {
                            "exec": {"command": health}, "periodSeconds": 10,
                            "timeoutSeconds": 4, "initialDelaySeconds": 10,
                        },
                    }],
                },
            },
        },
    }
    service = {
        "apiVersion": "v1", "kind": "Service", "metadata": metadata,
        "spec": {"selector": labels, "ports": [{"name": "http", "port": 8080, "targetPort": "http"}]},
    }
    return compose, [deployment, service]


def inspect_inventory(inventory):
    b.keys(inventory, ("schema", "services"), "participating fleet inventory")
    b.require(type(inventory["schema"]) is int and inventory["schema"] == 1, "Unsupported fleet inventory schema")
    services = inventory["services"]
    b.keys(services, ("portal",), "Portal TEST fleet")
    portal = services["portal"]
    b.keys(portal, ("repository", "channel"), "Portal owner source")
    b.require(portal == {"repository": PORTAL_IMAGE, "channel": "test"}, "Expected Portal's owner TEST channel")
    return portal


def generate(inventory, qualified, candidate, output):
    portal = inspect_inventory(inventory)
    b.keys(qualified, ("selector", "image", "version", "source_revision"), "Portal qualified image")
    b.require(qualified["selector"] == f'{portal["repository"]}:{portal["channel"]}'
              and b.matches(re.escape(portal["repository"]) + "@" + b.DIGEST, qualified["image"]),
              "Qualified Portal image must come from its declared owner channel")
    b.require(b.matches(b.VERSION, qualified["version"])
              and b.matches(r"[a-f0-9]{40}", qualified["source_revision"]), "Invalid qualified Portal build identity")
    b.require(b.matches(r"0\.24\.0-test\.[1-9][0-9]*", candidate), "Expected generated fleet candidate identity")
    compose, deployments = portal_assets(qualified["image"])
    component = {key: qualified[key] for key in ("image", "version", "source_revision")}
    outputs = {
        "test.json": (json.dumps({"schema": 1, "components": {"portal": component}, "assurance": None}, indent=2) + "\n").encode(),
        "compose.json": (json.dumps({
            "services": {"portal": compose},
            "secrets": {PORTAL_AUTH_NAME: {"file": "${PORTAL_AUTH_SECRET_FILE:?provide the Portal auth secret file}"}},
        }, indent=2) + "\n").encode(),
        "chart.tgz": chart_bytes(candidate, deployments),
    }
    output = Path(output)
    output.mkdir(mode=0o700)
    for name, data in outputs.items():
        (output / name).write_bytes(data)
