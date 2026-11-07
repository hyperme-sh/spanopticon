#!/usr/bin/env bash
set -euo pipefail
umask 077
if [[ $# != 6 ]]; then
  echo 'Usage: fetch-version.sh ACCOUNT CONTAINER BLOB VERSION_ID SHA256 NEW_DESTINATION' >&2
  exit 2
fi
account=$1 container=$2 blob=$3 version=$4 digest=$5 destination=$6
[[ -n $version && $digest =~ ^[a-f0-9]{64}$ && ! -d $destination ]] || exit 2
staged=$(mktemp -- "$(dirname -- "$destination")/.verification-restore.XXXXXXXX")
trap 'rm -f -- "$staged"' EXIT
az storage blob download --account-name "$account" --container-name "$container" \
  --name "$blob" --version-id "$version" --auth-mode login --file "$staged" \
  --only-show-errors --output none
actual=$(sha256sum < "$staged")
[[ ${actual%% *} == "$digest" ]] || { echo 'Archive digest mismatch' >&2; exit 1; }
ln -T -- "$staged" "$destination"
