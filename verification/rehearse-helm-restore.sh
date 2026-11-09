#!/usr/bin/env bash
# Called only in the disposable kind cluster created by qualify-helm.sh.
set -euo pipefail
image=${1:?}
archive=${2:?}
claim=contact-data
claim_uid() { kubectl get pvc "$claim" -o jsonpath='{.metadata.uid}'; }
mount_claim() {
  kubectl apply -f - <<YAML
apiVersion: v1
kind: Pod
metadata: {name: restore-helper}
spec:
  automountServiceAccountToken: false
  securityContext: {runAsNonRoot: true, runAsUser: 10001, runAsGroup: 10001, fsGroup: 10001, seccompProfile: {type: RuntimeDefault}}
  containers:
    - name: copy
      image: "$image"
      command: [sleep, "600"]
      securityContext: {allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: {drop: [ALL]}}
      volumeMounts: [{name: data, mountPath: /data}]
  volumes: [{name: data, persistentVolumeClaim: {claimName: $claim}}]
YAML
  kubectl wait --for=condition=Ready pod/restore-helper --timeout=120s
}
[[ $(kubectl config current-context) == kind-verification-* ]]
old_uid=$(claim_uid)
kubectl scale deployment/contact --replicas=0
kubectl wait --for=delete pod -l app.kubernetes.io/instance=contact --timeout=120s
mount_claim
kubectl exec restore-helper -- tar -C /data -cf - state mailbox > "$archive"
kubectl delete pod restore-helper --wait
# Preserve the chart's claim metadata and storage request while dropping its binding.
kubectl get pvc "$claim" -o json | jq '{apiVersion,kind,metadata:(.metadata|{name,labels,annotations:(.annotations|with_entries(select(.key|startswith("meta.helm.sh/"))))}),spec:(.spec|del(.volumeName))}' > "$archive.claim"
kubectl delete pvc "$claim" --wait
kubectl create -f "$archive.claim"
[[ $(claim_uid) != "$old_uid" ]]
mount_claim
kubectl exec restore-helper -- test ! -e /data/state/contact.db
kubectl exec -i restore-helper -- tar -C /data --no-same-owner -xf - < "$archive"
kubectl delete pod restore-helper --wait
kubectl scale deployment/contact --replicas=1
kubectl rollout status deployment/contact --timeout=180s
