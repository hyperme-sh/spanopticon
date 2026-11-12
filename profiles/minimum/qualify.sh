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
  if [[ -n ${realm_volume:-} ]]; then docker volume rm "$realm_volume" >/dev/null || result=1; fi
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
    realm_volume=$(docker volume create)
    start_keycloak() {
      keycloak_id=$(docker run -d -p "$1:8080" --mount "type=volume,src=$realm_volume,dst=/opt/keycloak/data" \
        --mount "type=bind,src=$2/realm.json,dst=/opt/keycloak/data/import/minimum.json,readonly" \
        quay.io/keycloak/keycloak:26.5@sha256:8d44614c74798322c4e07fbe0ecb15cfbb5879d69b484628555f58ade06f0d8c start-dev --import-realm)
      issuer_endpoint=$(docker port "$keycloak_id" 8080/tcp)
      export IDENTITY_ISSUER="http://$issuer_endpoint/realms/minimum" IDENTITY_AUDIENCE=minimum-api IDENTITY_ALGORITHM=RS256
      curl -fsS --retry 36 --retry-all-errors --retry-delay 2 "$IDENTITY_ISSUER/.well-known/openid-configuration" -o "$root/discovery.json"
      jq -e --arg expected "$IDENTITY_ISSUER" '.issuer == $expected' "$root/discovery.json" >/dev/null
    }
    start_keycloak "127.0.0.1:" "$root/config"
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
    repository=docker.io/library/minimum-reference
    image="$repository:$(git rev-parse HEAD)"
    if helm template minimum "$profile/chart" --set-string image="evil.invalid/other:test" --set-string imageRepository="$repository" --set tenant=test --set existingSecret=test > "$root/render.log" 2>&1; then
      echo 'Chart accepted an unapproved repository' >&2
      exit 1
    fi
    grep -q 'image must use the approved repository' "$root/render.log"
    kubectl create secret generic minimum-config "${secret_files[@]}"
    helm install minimum "$profile/chart" --set-string image="$image" --set-string imageRepository="$repository" --set imagePullPolicy=Never "${helm_identity[@]}" --set existingSecret=minimum-config --set-string tenant="$tenant" --wait --timeout 3m
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
  python3 "$profile/keycloak_check.py" check "$root/config" --issuer "$IDENTITY_ISSUER" --api "http://$api" > "$root/before.json"
  python3 "$profile/keycloak_check.py" prepare "$root/changed" --audience changed-api
  for config in "$root/config" "$root/changed"; do
    docker stop "$keycloak_id" >/dev/null
    docker rm "$keycloak_id" >/dev/null
    keycloak_id=
    start_keycloak "$issuer_endpoint" "$config"
    python3 "$profile/keycloak_check.py" check "$root/config" --issuer "$IDENTITY_ISSUER" --api "http://$api" > "$root/after.json"
    cmp "$root/before.json" "$root/after.json"
  done
  python3 "$profile/keycloak_check.py" check-rejected "$root/changed" --issuer "$IDENTITY_ISSUER"
else
  python3 "$profile/check.py" --api "http://$api" --issuer "http://$issuer" --accounts "$root/config/clients.json"
fi
