#!/usr/bin/env bash
# Verify the label in GHCR and deploy the pinned manifests with each native engine.
set -euo pipefail
[[ $CANDIDATE =~ ^0\.24\.0-test\.[1-9][0-9]*$ ]]
[[ $ENGINE == compose || $ENGINE == helm ]]
umask 077
scratch=$(mktemp -d)
export DOCKER_CONFIG="$scratch/docker" KUBECONFIG="$scratch/kubeconfig"
mkdir "$DOCKER_CONFIG"
out="$RUNNER_TEMP/published-deployment"
mkdir -p "$out"
project="registry-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
compose=(docker compose -p "$project" -f fleet/gateway/compose.yaml -f fleet/plane/compose.yaml)
forward_pid=
cluster_created=false
compose_started=false
cleanup() {
  local result=$?
  if [[ -n $forward_pid ]]; then kill "$forward_pid" || true; fi
  if $compose_started; then "${compose[@]}" down || result=1; fi
  if $cluster_created; then kind delete cluster --name "$project" || result=1; fi
  rm -rf -- "$scratch"
  exit "$result"
}
trap cleanup EXIT
python3 - <<'PY'
import base64, json, os
from pathlib import Path
auth = base64.b64encode(f"{os.environ['GITHUB_ACTOR']}:{os.environ['GHCR_TOKEN']}".encode()).decode()
(Path(os.environ['DOCKER_CONFIG']) / 'config.json').write_text(json.dumps({'auths': {'ghcr.io': {'auth': auth}}}))
PY
unset GHCR_TOKEN
export TEST_POSTGRES_PASSWORD=$(openssl rand -hex 24) TEST_TICKET_KEY=$(openssl rand -hex 32)
for owner in gateway plane; do
  pin="fleet/owner-images/hypermesh-$owner.json"
  image=$(jq -er .destination "$pin")
  expected=$(jq -er .manifest_digest "$pin")
  docker buildx imagetools inspect --raw "$image:$CANDIDATE" > "$scratch/$owner.manifest"
  actual="sha256:$(sha256sum "$scratch/$owner.manifest" | cut -d' ' -f1)"
  [[ $actual == "$expected" ]]
done

if [[ $ENGINE == compose ]]; then
  compose_started=true
  "${compose[@]}" pull
  "${compose[@]}" up --no-build --pull never -d --wait --wait-timeout 180
else
  cluster_created=true
  kind create cluster --name "$project" --image kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5 --wait 120s
  kubectl create secret generic registry-read --type=kubernetes.io/dockerconfigjson \
    --from-file=".dockerconfigjson=$DOCKER_CONFIG/config.json"
  kubectl patch serviceaccount default -p '{"imagePullSecrets":[{"name":"registry-read"}]}'
  python3 - <<'PY' | kubectl create -f -
import json, os
password = os.environ['TEST_POSTGRES_PASSWORD']
print(json.dumps({'apiVersion':'v1','kind':'Secret','metadata':{'name':'plane-test'},
                  'stringData':{'postgres-password':password,'ticket-key':os.environ['TEST_TICKET_KEY'],
                                'database-url':f'postgresql+psycopg://plane_test:{password}@plane-postgres:5432/plane_test'}}))
PY
  helm install gateway fleet/gateway/chart --wait --timeout 3m
  helm install plane fleet/plane/chart --set existingSecret=plane-test --wait --timeout 4m
fi
for owner in gateway plane; do
  pin="fleet/owner-images/hypermesh-$owner.json"
  expected=$(jq -er '.destination+"@"+.manifest_digest' "$pin")
  source=$(jq -er .source_revision "$pin")
  if [[ $ENGINE == compose ]]; then
    container=$("${compose[@]}" ps -q "$owner")
    [[ $(docker inspect -f '{{.Config.Image}}' "$container") == "$expected" ]]
    [[ $(docker inspect -f '{{.Image}}' "$container") == "$(jq -er .config_digest "$pin")" ]]
    endpoint=$("${compose[@]}" port "$owner" 8000)
  else
    deployment="$owner-$owner"
    [[ $(kubectl get deployment "$deployment" -o "jsonpath={.spec.template.spec.containers[0].image}") == "$expected" ]]
    kubectl port-forward "service/$deployment" 18000:8000 > "$scratch/forward.log" 2>&1 &
    forward_pid=$!
    endpoint=127.0.0.1:18000
  fi
  curl --fail --silent --show-error --retry 12 --retry-connrefused "http://$endpoint/version" | jq -e --arg sha "$source" '.git_sha == $sha' >/dev/null
  if [[ $owner == plane ]]; then
    PYTHONPATH=scripts python3 -c 'from qualify_plane_runtime import probe; import sys; probe(*sys.argv[1:])' "http://$endpoint" "$source"
  else
    curl --fail --silent --show-error "http://$endpoint/api/v1/health/" >/dev/null
  fi
  if [[ -n $forward_pid ]]; then kill "$forward_pid"; wait "$forward_pid" || true; forward_pid=; fi
  jq -n --arg owner "$owner" --arg engine "$ENGINE" --arg candidate "$CANDIDATE" --arg image "$expected" \
    --arg source "$source" --arg revision "$GITHUB_SHA" \
    '{owner:$owner,engine:$engine,candidate:$candidate,image:$image,source_revision:$source,workflow_revision:$revision,health_and_identity_verified:true}' > "$out/$owner.json"
done
