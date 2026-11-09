#!/usr/bin/env bash
set -euo pipefail
umask 077
tmp=$(mktemp -d)
container=
cleanup() {
  if [[ -n $container ]]; then docker rm -f "$container" >/dev/null; fi
  rm -rf -- "$tmp"
}
trap cleanup EXIT
ssh-keygen -q -N '' -t ed25519 -f "$tmp/client"
chmod 644 "$tmp/client.pub"
docker build -t verification-backup-ci -f verification/fixtures/backup/Dockerfile .
container=$(docker run -d -p 127.0.0.1::22 --mount "type=bind,src=$tmp/client.pub,dst=/run/replica.pub,readonly" verification-backup-ci)
port=$(docker port "$container" 22/tcp)
for attempt in {1..30}; do
  docker exec "$container" cat /etc/ssh/ssh_host_ed25519_key.pub > "$tmp/host.pub" 2>/dev/null && break
  sleep 1
done
echo "[127.0.0.1]:${port##*:} $(cat "$tmp/host.pub")" > "$tmp/known"
cat > "$tmp/config" <<CONFIG
Host replica
  HostName 127.0.0.1
  Port ${port##*:}
  User replica
  IdentityFile $tmp/client
  IdentitiesOnly yes
  BatchMode yes
  HostKeyAlgorithms ssh-ed25519
  StrictHostKeyChecking yes
  UserKnownHostsFile $tmp/known
CONFIG
VERIFICATION_CI_SSH_CONFIG="$tmp/config" python3 -m verification.qualify
