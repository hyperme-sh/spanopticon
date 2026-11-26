"""Render enterprise fleet configuration from enrolled service tags."""
import json

from deployment import bundle as b
from deployment.generate import chart_bytes
from scripts.service_channels import reference

PORTAL_IMAGE = "ghcr.io/hyperme-sh/controlplane-portal/runtime"
PORTAL_HEALTH = (
    'fetch("http://127.0.0.1:8080/signed-out",{redirect:"error",'
    'signal:AbortSignal.timeout(3000)}).then(r=>{if(!r.ok) process.exitCode=1})'
    '.catch(()=>{process.exitCode=1})'
)
PORTAL_AUTH_NAME = "portal-auth"
PORTAL_SETTINGS_NAME = "portal-settings"
FLEET_PULL_SECRET = "fleet-ghcr"
# A Helm upgrade recreates the Portal pod even when its image selector is
# unchanged. Kubernetes then pulls the image currently published at that tag.
PORTAL_CHART_TEMPLATE = b'''{{- $resources := .Files.Get "resources.json" | fromJson -}}
{{- range $resources.items }}
{{- $_ := set .metadata "namespace" $.Release.Namespace }}
{{- if eq .kind "Deployment" }}
{{- $_ := set .spec.template.metadata "annotations" (dict "spanopticon.sh/helm-revision" (toString $.Release.Revision)) }}
{{- end }}
---
{{ toJson . }}
{{ end -}}
'''
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
        "pull_policy": "always",
        "environment": {
            "AUTH_TRUST_HOST": "true",
            "TMPDIR": "/run/portal-tmp",
            **{key: "${" + key + ":?provide Portal runtime configuration}" for key in PORTAL_RUNTIME_SETTINGS},
        },
        "secrets": [PORTAL_AUTH_NAME],
        "entrypoint": [
            "/bin/sh", "-ec",
            'AUTH_SECRET="$$(cat /run/secrets/portal-auth)"; test -n "$$AUTH_SECRET"; '
            'export AUTH_SECRET; exec node server.js',
        ],
        "read_only": True,
        "tmpfs": ["/run/portal-tmp:rw,noexec,nosuid,size=64m,mode=0700,uid=1000,gid=1000"],
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
                        "runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000, "fsGroup": 1000,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}}],
                    "containers": [{
                        "name": "portal",
                        "image": image,
                        "imagePullPolicy": "Always",
                        "ports": [{"name": "http", "containerPort": 8080}],
                        "env": [
                            {"name": "AUTH_TRUST_HOST", "value": "true"},
                            {"name": "TMPDIR", "value": "/run/portal-tmp"},
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
                        "volumeMounts": [{"name": "tmp", "mountPath": "/run/portal-tmp"}],
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
    b.require(portal["repository"] == PORTAL_IMAGE and isinstance(portal["channel"], str),
              "Expected the Portal owner's registry and an explicit channel")
    return reference(f'{portal["repository"]}:{portal["channel"]}', PORTAL_IMAGE)


def generate(inventory, chart_version):
    selector = inspect_inventory(inventory)
    b.require(b.matches(b.VERSION + r"(?:-test\.[1-9][0-9]*)?", chart_version),
              "Expected a chart version")
    compose, deployments = portal_assets(selector)
    runtime_inputs = {
        "settings": {"helm_config_map": PORTAL_SETTINGS_NAME, "keys": list(PORTAL_RUNTIME_SETTINGS)},
        "secret": {
            "compose_file_env": "PORTAL_AUTH_SECRET_FILE",
            "helm": {"name": PORTAL_AUTH_NAME, "key": "token"},
        },
        "helm_image_pull_secret": FLEET_PULL_SECRET,
    }
    return {
        "compose.json": (json.dumps({
            "services": {"portal": compose},
            "secrets": {PORTAL_AUTH_NAME: {"file": "${PORTAL_AUTH_SECRET_FILE:?provide the Portal auth secret file}"}},
        }, indent=2) + "\n").encode(),
        "chart.tgz": chart_bytes(chart_version, deployments, template=PORTAL_CHART_TEMPLATE),
        "runtime-inputs.json": (json.dumps(runtime_inputs, indent=2) + "\n").encode(),
    }
