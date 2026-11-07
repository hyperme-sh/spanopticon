#!/usr/bin/env bash
# Run from the repository root on a disposable Linux Docker runner.
set -euo pipefail
root=$(mktemp -d)
project="minimum-$(basename "$root" | tr '[:upper:].' '[:lower:]-')"
engine=${1:?choose compose or helm}
profile=profiles/minimum
sha256sum --check "$profile/deployment.sha256"
compose=(docker compose --project-name "$project" --env-file "$root/config/compose.env" -f "$profile/compose.yaml" --profile local-issuer)
pids=()
cleanup() {
  result=$?
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  case $engine in
    compose) "${compose[@]}" down --volumes || result=1 ;;
    helm) kind delete cluster --name "$project" || result=1 ;;
  esac
  rm -rf -- "$root"
  exit "$result"
}
python3 "$profile/setup.py" "$root/config"
trap cleanup EXIT
case $engine in
  compose)
    "${compose[@]}" up --build -d --wait
    api=$("${compose[@]}" port api 8000)
    issuer=$("${compose[@]}" port identity 8000)
    ;;
  helm)
    export KUBECONFIG="$root/kubeconfig"
    image="minimum-reference:$(git rev-parse HEAD)"
    docker build -f "$profile/Dockerfile" -t "$image" .
    kind create cluster --name "$project" --image kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5 --wait 120s
    kind load docker-image --name "$project" "$image"
    kubectl create secret generic minimum-config --from-file="$root/config/issuer.pub" --from-file="$root/config/issuer.key" --from-file="$root/config/accounts.json"
    helm install minimum "$profile/chart" --set-string image="$image" --set localIssuer=true --set existingSecret=minimum-config --set-string tenant="$(jq -r .owner.tenant_id "$root/config/clients.json")" --wait --timeout 3m
    for role in api identity; do
      kubectl port-forward "service/minimum-$role" :8000 > "$root/$role.log" 2>&1 &
      pids+=("$!")
      for attempt in {1..30}; do
        grep -q 'Forwarding from 127.0.0.1:' "$root/$role.log" && break
        sleep 1
      done
    done
    api=$(sed -n 's/^Forwarding from \(127.0.0.1:[0-9]*\).*/\1/p' "$root/api.log" | head -1)
    issuer=$(sed -n 's/^Forwarding from \(127.0.0.1:[0-9]*\).*/\1/p' "$root/identity.log" | head -1)
    ;;
  *) exit 2 ;;
esac
python3 "$profile/check.py" --api "http://$api" --issuer "http://$issuer" --accounts "$root/config/clients.json"
