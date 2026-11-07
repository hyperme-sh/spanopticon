#!/usr/bin/env bash
# Run from the repository root on a disposable Linux Docker runner.
set -euo pipefail
root=$(mktemp -d)
project="minimum-$(basename "$root" | tr '[:upper:].' '[:lower:]-')"
engine=${1:?choose compose or helm}
identity_mode=${2:-local}
profile=profiles/minimum
sha256sum --check "$profile/deployment.sha256"
compose=(docker compose --project-name "$project" --env-file "$root/config/compose.env" -f "$profile/compose.yaml" )
pids=()
cleanup() {
  result=$?
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  case $engine in
    compose) "${compose[@]}" down --volumes || result=1 ;;
    helm) kind delete cluster --name "$project" || result=1 ;;
  esac
  if [[ -n ${keycloak_id:-} ]]; then docker rm -f "$keycloak_id" >/dev/null || result=1; fi
  rm -rf -- "$root"
  exit "$result"
}
trap cleanup EXIT
roles=(api)
secret_files=(--from-file="$root/config/issuer.pub")
helm_identity=()
case $identity_mode in
  local)
    python3 "$profile/setup.py" "$root/config"
    roles+=(identity)
    compose+=(--profile local-issuer)
    helm_identity+=(--set localIssuer=true)
    secret_files+=(--from-file="$root/config/issuer.key" --from-file="$root/config/accounts.json")
    tenant=$(jq -r .owner.tenant_id "$root/config/clients.json")
    ;;
  keycloak)
    python3 "$profile/keycloak_check.py" prepare "$root/config"
    keycloak_id=$(docker run -d -p 127.0.0.1::8080 --mount "type=bind,src=$root/config/realm.json,dst=/opt/keycloak/data/import/minimum.json,readonly" \
      quay.io/keycloak/keycloak:26.5@sha256:8d44614c74798322c4e07fbe0ecb15cfbb5879d69b484628555f58ade06f0d8c start-dev --import-realm)
    issuer_endpoint=$(docker port "$keycloak_id" 8080/tcp)
    export IDENTITY_ISSUER="http://$issuer_endpoint/realms/minimum" IDENTITY_AUDIENCE=minimum-api IDENTITY_ALGORITHM=RS256
    curl -fsS --retry 36 --retry-all-errors --retry-delay 2 "$IDENTITY_ISSUER/.well-known/openid-configuration" -o "$root/discovery.json"
    jq -e --arg expected "$IDENTITY_ISSUER" '.issuer == $expected' "$root/discovery.json" >/dev/null
    python3 "$profile/keycloak_check.py" trust "$root/config" --issuer "$IDENTITY_ISSUER"
    helm_identity+=(--set-string issuer="$IDENTITY_ISSUER" --set-string audience="$IDENTITY_AUDIENCE" --set-string algorithm=RS256)
    tenant=$(cat "$root/config/tenant")
    ;;
  *) exit 2 ;;
esac
case $engine in
  compose)
    "${compose[@]}" up --build -d --wait
    api=$("${compose[@]}" port api 8000)
    if [[ $identity_mode == local ]]; then issuer=$("${compose[@]}" port identity 8000); fi
    ;;
  helm)
    export KUBECONFIG="$root/kubeconfig"
    image="minimum-reference:$(git rev-parse HEAD)"
    docker build -f "$profile/Dockerfile" -t "$image" .
    kind create cluster --name "$project" --image kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5 --wait 120s
    kind load docker-image --name "$project" "$image"
    # Use the imported manifest digest (the Docker image ID is a config digest).
    repository=docker.io/library/minimum-reference
    node="$project-control-plane"
    digest=$(docker exec "$node" ctr -n k8s.io images ls | awk -v ref="docker.io/library/$image" '$1 == ref {print $3}')
    [[ $digest =~ ^sha256:[0-9a-f]{64}$ ]]
    pinned="$repository@$digest"
    docker exec "$node" ctr -n k8s.io images tag --force "docker.io/library/$image" "$pinned"
    if helm template minimum "$profile/chart" --set-string image="$image" --set tenant=test --set existingSecret=test > "$root/render.log" 2>&1; then
      echo 'Chart accepted a mutable image tag' >&2
      exit 1
    fi
    grep -q 'image must be a digest-pinned reference' "$root/render.log"
    kubectl create secret generic minimum-config "${secret_files[@]}"
    helm install minimum "$profile/chart" --set-string image="$pinned" "${helm_identity[@]}" --set existingSecret=minimum-config --set-string tenant="$tenant" --wait --timeout 3m
    for role in "${roles[@]}"; do
      kubectl port-forward "service/minimum-$role" :8000 > "$root/$role.log" 2>&1 &
      pids+=("$!")
      for attempt in {1..30}; do
        grep -q 'Forwarding from 127.0.0.1:' "$root/$role.log" && break
        sleep 1
      done
    done
    api=$(sed -n 's/^Forwarding from \(127.0.0.1:[0-9]*\).*/\1/p' "$root/api.log" | head -1)
    if [[ $identity_mode == local ]]; then issuer=$(sed -n 's/^Forwarding from \(127.0.0.1:[0-9]*\).*/\1/p' "$root/identity.log" | head -1); fi
    ;;
  *) exit 2 ;;
esac
if [[ $identity_mode == keycloak ]]; then
  python3 "$profile/keycloak_check.py" check "$root/config" --issuer "$IDENTITY_ISSUER" --api "http://$api"
else
  python3 "$profile/check.py" --api "http://$api" --issuer "http://$issuer" --accounts "$root/config/clients.json"
fi
