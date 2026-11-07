#!/usr/bin/env bash
set -euo pipefail
umask 077
root=$(mktemp -d)
cluster="verification-$(openssl rand -hex 5)"
export KUBECONFIG="$root/kubeconfig"
issuer_id=
forward=
cleanup() {
  status=$?
  if [[ -n $forward ]]; then kill "$forward" || true; fi
  if [[ -n $issuer_id ]]; then docker rm -f "$issuer_id" >/dev/null || status=1; fi
  kind delete cluster --name "$cluster" || status=1
  rm -rf -- "$root"
  exit "$status"
}
trap cleanup EXIT
cfg="$root/config"
python3 profiles/minimum/keycloak_check.py prepare "$cfg" --audience verification-api
issuer_id=$(docker run -d -p 127.0.0.1::8080 --mount "type=bind,src=$cfg/realm.json,dst=/opt/keycloak/data/import/check.json,readonly" \
  quay.io/keycloak/keycloak:26.5@sha256:8d44614c74798322c4e07fbe0ecb15cfbb5879d69b484628555f58ade06f0d8c start-dev --import-realm)
issuer="http://$(docker port "$issuer_id" 8080/tcp)/realms/minimum"
curl -fsS --retry 45 --retry-all-errors --retry-delay 2 "$issuer/.well-known/openid-configuration" -o "$root/discovery.json"
jq -e --arg issuer "$issuer" '.issuer==$issuer' "$root/discovery.json" >/dev/null
python3 profiles/minimum/keycloak_check.py trust "$cfg" --issuer "$issuer"
openssl rand 32 > "$cfg/secret"
image="local-contact:$(git rev-parse HEAD)"
docker build -f verification/Dockerfile -t "$image" .
kind create cluster --name "$cluster" --image kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5 --wait 120s
kind load docker-image --name "$cluster" "$image"
node="$cluster-control-plane"
ref="docker.io/library/$image"
digest=$(docker exec "$node" ctr -n k8s.io images ls | awk -v ref="$ref" '$1 == ref {print $3}')
[[ $digest =~ ^sha256:[0-9a-f]{64}$ ]]
pinned="docker.io/library/local-contact@$digest"
docker exec "$node" ctr -n k8s.io images tag --force "$ref" "$pinned"
kubectl create secret generic verification-config --from-file="$cfg/issuer.pub" --from-file="$cfg/secret"
helm install contact verification/chart --set-string image="$pinned" --set existingSecret=verification-config \
  --set-string issuer="$issuer" --set audience=verification-api --wait --timeout 3m
kubectl port-forward service/contact :8000 > "$root/forward.log" 2>&1 &
forward=$!
for attempt in {1..30}; do
  grep -q 'Forwarding from 127.0.0.1:' "$root/forward.log" && break
  sleep 1
done
endpoint=$(sed -n 's/^Forwarding from \(127.0.0.1:[0-9]*\).*/\1/p' "$root/forward.log" | head -1)
python3 -m verification.keycloak_helm_check "$cfg" "$issuer" "http://$endpoint"
