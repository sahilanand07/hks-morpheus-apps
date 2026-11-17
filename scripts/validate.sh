#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# validate.sh - everything that can be checked without the cluster.
# Runs locally and in GitHub Actions (.github/workflows/validate.yml).
#
#   1. Spec checks          scripts/check_specs.py (Morpheus-specific rules)
#   2. Kubernetes schemas   kubeconform -strict against Kubernetes K8S_VERSION
#   3. nginx + page render  scripts/test-pages.sh (needs docker)
#   4. AI assistant app     pytest images/doc-assistant/tests (stand-in model server)
#
# Usage: bash scripts/validate.sh       K8S_VERSION=1.35.0 by default (the lab cluster)
# Steps whose tool is missing are reported as SKIP; any FAIL makes the exit code 1.
# ------------------------------------------------------------------------------
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
K8S_VERSION="${K8S_VERSION:-1.35.0}"
rc=0

echo "== 1/4 Spec checks"
python3 scripts/check_specs.py || rc=1

echo
echo "== 2/4 Kubernetes schemas (kubeconform, Kubernetes $K8S_VERSION)"
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
echo "== 3/4 nginx config and page render"
if docker info >/dev/null 2>&1; then
  bash scripts/test-pages.sh || rc=1
else
  echo "  SKIP  docker not available"
fi

echo
echo "== 4/4 Private AI Assistant app (pytest, no models needed)"
if python3 -c "import fastapi, httpx, numpy, pypdf, multipart, pytest" >/dev/null 2>&1; then
  python3 -m pytest -q -p no:cacheprovider images/doc-assistant/tests || rc=1
else
  echo "  SKIP  install the app's packages first: pip install -r images/doc-assistant/requirements.txt pytest"
fi

echo
if (( rc )); then echo "Validation FAILED."; else echo "Validation passed."; fi
exit "$rc"
