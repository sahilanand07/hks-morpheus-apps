#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# validate.sh - everything that can be checked without the cluster.
# Runs locally and in GitHub Actions (.github/workflows/validate.yml).
#
#   1. Spec checks          scripts/check_specs.py (Morpheus-specific rules)
#   2. Kubernetes schemas   kubeconform -strict against Kubernetes K8S_VERSION
#   3. nginx + page render  scripts/test-pages.sh (needs docker)
#
# Usage: bash scripts/validate.sh       K8S_VERSION=1.35.0 by default (the lab cluster)
# Steps whose tool is missing are reported as SKIP; any FAIL makes the exit code 1.
# ------------------------------------------------------------------------------
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
K8S_VERSION="${K8S_VERSION:-1.35.0}"
rc=0

echo "== 1/3 Spec checks"
python3 scripts/check_specs.py || rc=1

echo
echo "== 2/3 Kubernetes schemas (kubeconform, Kubernetes $K8S_VERSION)"
if command -v kubeconform >/dev/null 2>&1; then
  kubeconform -strict -summary -kubernetes-version "$K8S_VERSION" apps/*/*.yaml cluster/*.yaml || rc=1
  if command -v kubectl >/dev/null 2>&1; then
    echo "  kustomize output (kubectl kustomize .):"
    kubectl kustomize . | kubeconform -strict -summary -kubernetes-version "$K8S_VERSION" - || rc=1
  else
    echo "  SKIP  kustomize output (kubectl not installed)"
  fi
else
  echo "  SKIP  kubeconform not installed (https://github.com/yannh/kubeconform)"
fi

echo
echo "== 3/3 nginx config and page render"
if docker info >/dev/null 2>&1; then
  bash scripts/test-pages.sh || rc=1
else
  echo "  SKIP  docker not available"
fi

echo
if (( rc )); then echo "Validation FAILED."; else echo "Validation passed."; fi
exit "$rc"
