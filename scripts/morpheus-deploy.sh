#!/usr/bin/env bash
# ==============================================================================
# morpheus-deploy.sh - deploy, update, check or remove the demo apps with kubectl.
#
# Made for a Morpheus Shell Script task that SSHes to the HKS master
# (Execute Target: Remote). It sidesteps the Morpheus 9.1 Kubernetes App
# blueprint bugs (see PLAYBOOK.md) and gives Morpheus an honest result: the
# task output shows every step, and the exit code fails the task when the
# rollout fails. Works from any shell that has kubectl and cluster-admin.
#
#   morpheus-deploy.sh apply  [all|hpe-enterprise|hello-world]   create or update (rolling, no downtime)
#   morpheus-deploy.sh status [all|hpe-enterprise|hello-world]   readiness, version, URL
#   morpheus-deploy.sh delete [all|hpe-enterprise|hello-world]   remove the app's objects
#
# Specs come from the checkout this script sits in or, when it runs on its own
# (piped, or as a Morpheus Repository-sourced task), from a shallow clone of
# REPO_URL at REF.
#
# Env: NAMESPACE (hpe-apps)  REF (main)  REPO_URL  WAIT (rollout timeout in s, 180; 0 = don't wait)
#      KUBECONFIG (default: /etc/kubernetes/admin.conf, else ~/.kube/config)
#      PROXY_ENV_FILE (/etc/environment: read for *_proxy when the session has none)
# Not root and admin.conf unreadable? It re-runs itself with "sudo -n".
# Exit code 0 = everything OK, 1 = something failed.
# ==============================================================================
set -uo pipefail

ACTION="${1:-apply}"
TARGET="${2:-all}"
NAMESPACE="${NAMESPACE:-hpe-apps}"
REF="${REF:-main}"
REPO_URL="${REPO_URL:-https://github.com/sahilanand07/hks-morpheus-apps.git}"
WAIT="${WAIT:-180}"
PROXY_ENV_FILE="${PROXY_ENV_FILE:-/etc/environment}"
ALL_APPS="hpe-enterprise hello-world"
LABEL_PART_OF="app.kubernetes.io/part-of=hks-morpheus-apps"

log() { printf '%s  %s\n' "$(date -u +%H:%M:%S)" "$*"; }
indent() { sed 's/^/    /'; }
die() { log "ERROR: $*"; log "RESULT: FAILED"; exit 1; }

case "$ACTION" in
  apply|status|delete) ;;
  *) die "usage: $0 <apply|status|delete> [all|${ALL_APPS// /|}]" ;;
esac
if [[ "$TARGET" == "all" ]]; then
  APPS="$ALL_APPS"
elif [[ " $ALL_APPS " == *" $TARGET "* ]]; then
  APPS="$TARGET"
else
  die "unknown app '$TARGET' (choose: all $ALL_APPS)"
fi
[[ "$WAIT" =~ ^[0-9]+$ ]] || die "WAIT must be a number of seconds, got '$WAIT'"

# --- proxy: a Morpheus SSH task may run without /etc/environment loaded --------
if [[ -z "${https_proxy:-}${HTTPS_PROXY:-}" && -r "$PROXY_ENV_FILE" ]]; then
  while IFS='=' read -r key value; do
    case "$key" in
      http_proxy|https_proxy|no_proxy|HTTP_PROXY|HTTPS_PROXY|NO_PROXY)
        value="${value%\"}"; value="${value#\"}"
        export "$key=$value" ;;
    esac
  done < "$PROXY_ENV_FILE"
fi

# --- cluster access --------------------------------------------------------------
command -v kubectl >/dev/null 2>&1 || die "kubectl not found: run this on the HKS master"
if [[ -z "${KUBECONFIG:-}" ]]; then
  if [[ -r /etc/kubernetes/admin.conf ]]; then
    export KUBECONFIG=/etc/kubernetes/admin.conf
  elif [[ -e /etc/kubernetes/admin.conf && $EUID -ne 0 && -f "$0" ]]; then
    sudo -n true 2>/dev/null || die "/etc/kubernetes/admin.conf needs root and passwordless sudo is not available"
    log "re-running with sudo to read /etc/kubernetes/admin.conf"
    exec sudo -n env NAMESPACE="$NAMESPACE" REF="$REF" REPO_URL="$REPO_URL" WAIT="$WAIT" \
      PROXY_ENV_FILE="$PROXY_ENV_FILE" ${SRC_DIR:+SRC_DIR="$SRC_DIR"} bash "$0" "$@"
  elif [[ -r "$HOME/.kube/config" ]]; then
    export KUBECONFIG="$HOME/.kube/config"
  fi
fi
kubectl version --request-timeout=10s >/dev/null 2>&1 \
  || die "cannot reach the Kubernetes API (KUBECONFIG=${KUBECONFIG:-unset}); check the kubeconfig and NO_PROXY"

# --- specs: this checkout, or a fresh clone --------------------------------------
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [[ -n "${SRC_DIR:-}" ]]; then
  :
elif [[ -n "$script_dir" && -d "$script_dir/../apps" ]]; then
  SRC_DIR="$script_dir/.."
elif [[ "$ACTION" == "apply" || "$ACTION" == "delete" ]]; then
  command -v git >/dev/null 2>&1 || die "git not found and no local checkout of the repo"
  workdir="$(mktemp -d)"
  trap 'rm -rf "$workdir"' EXIT
  log "cloning $REPO_URL (ref $REF)"
  git clone -q --depth 1 --branch "$REF" "$REPO_URL" "$workdir/repo" \
    || die "git clone failed: check the proxy (https_proxy=${https_proxy:-unset}) and that ref '$REF' exists"
  SRC_DIR="$workdir/repo"
fi
if [[ -n "${SRC_DIR:-}" ]]; then
  SRC_DIR="$(cd "$SRC_DIR" && pwd)"
  COMMIT="$(git -C "$SRC_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
fi

node_ips() {
  kubectl get nodes -o jsonpath='{range .items[*]}{.status.addresses[?(@.type=="InternalIP")].address}{" "}{end}' 2>/dev/null
}

http_check() {   # http_check <app> <nodeport>: GET / on one node, print pod and version
  local app=$1 port=$2 ip body pod ver
  ip="$(node_ips | awk '{print $NF}')"
  [[ -n "$ip" && -n "$port" ]] || { log "  $app: no node IP or NodePort to test"; return 0; }
  command -v curl >/dev/null 2>&1 || { log "  $app: curl not installed, skipping HTTP check"; return 0; }
  body="$(curl --noproxy '*' -s -m 5 "http://$ip:$port/" 2>/dev/null || true)"
  pod="$(sed -n 's/.*id="pod">\([^<]*\)<.*/\1/p' <<<"$body" | head -1)"
  ver="$(sed -n 's/.*id="version">\([^<]*\)<.*/\1/p' <<<"$body" | head -1)"
  if [[ -n "$pod" ]]; then
    log "  $app: http://$ip:$port/ answered from pod $pod (version $ver)"
  else
    log "  $app: http://$ip:$port/ did not answer yet"
    return 1
  fi
}

show_failure() {   # diagnostics for a rollout that did not finish
  local app=$1
  kubectl -n "$NAMESPACE" get pods -l "app.kubernetes.io/name=$app" -o wide 2>&1 | indent
  kubectl -n "$NAMESPACE" get events --sort-by=.lastTimestamp 2>/dev/null | grep -E "$app" | tail -8 | indent
}

apply_app() {
  local app=$1 file="$SRC_DIR/apps/$1/$1.yaml" version out port
  [[ -f "$file" ]] || { log "FAIL $app: $file not found"; return 1; }
  version="$(sed -n 's/.*hks-morpheus-apps\/version: "\([^"]*\)".*/\1/p' "$file" | head -1)"
  log "$app: applying version ${version:-?} (commit $COMMIT) to namespace $NAMESPACE"
  if ! out="$(kubectl -n "$NAMESPACE" apply -f "$file" 2>&1)"; then
    indent <<<"$out"
    log "FAIL $app: kubectl apply was rejected"
    return 1
  fi
  indent <<<"$out"
  if (( WAIT > 0 )); then
    if ! out="$(kubectl -n "$NAMESPACE" rollout status "deploy/$app" --timeout="${WAIT}s" 2>&1)"; then
      indent <<<"$out"
      log "FAIL $app: not ready after ${WAIT}s"
      show_failure "$app"
      return 1
    fi
    log "  $app: rollout complete"
    port="$(kubectl -n "$NAMESPACE" get svc "$app" -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null)"
    http_check "$app" "$port" || return 1
  fi
  log "OK   $app"
}

status_app() {
  local app=$1 ready want version port
  if ! kubectl -n "$NAMESPACE" get deploy "$app" >/dev/null 2>&1; then
    log "MISSING $app: no Deployment in namespace $NAMESPACE"
    return 1
  fi
  ready="$(kubectl -n "$NAMESPACE" get deploy "$app" -o jsonpath='{.status.readyReplicas}')"
  want="$(kubectl -n "$NAMESPACE" get deploy "$app" -o jsonpath='{.spec.replicas}')"
  version="$(kubectl -n "$NAMESPACE" get deploy "$app" -o jsonpath='{.spec.template.metadata.annotations.hks-morpheus-apps/version}')"
  port="$(kubectl -n "$NAMESPACE" get svc "$app" -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null)"
  log "$app: ${ready:-0}/$want ready, version ${version:-?}, NodePort ${port:-none}"
  kubectl -n "$NAMESPACE" get pods -l "app.kubernetes.io/name=$app" -o wide 2>&1 | indent
  [[ "${ready:-0}" == "$want" ]] || { log "NOT READY $app"; return 1; }
  http_check "$app" "$port" || return 1
  log "OK   $app"
}

delete_app() {
  local app=$1 out
  log "$app: deleting Deployment, Service and ConfigMap in namespace $NAMESPACE"
  if ! out="$(kubectl -n "$NAMESPACE" delete deploy,svc,cm -l "app.kubernetes.io/name=$app,$LABEL_PART_OF" --ignore-not-found 2>&1)"; then
    indent <<<"$out"
    log "FAIL $app: delete failed"
    return 1
  fi
  [[ -n "$out" ]] && indent <<<"$out"
  log "OK   $app removed"
}

log "hks-morpheus-apps: $ACTION $TARGET (namespace $NAMESPACE${COMMIT:+, commit $COMMIT})"
if [[ "$ACTION" == "apply" ]]; then
  if ! kubectl get ns "$NAMESPACE" >/dev/null 2>&1; then
    log "creating namespace $NAMESPACE"
    kubectl create namespace "$NAMESPACE" >/dev/null || die "could not create namespace $NAMESPACE"
  fi
  kubectl label ns "$NAMESPACE" --overwrite \
    pod-security.kubernetes.io/enforce=restricted pod-security.kubernetes.io/warn=restricted >/dev/null \
    || log "WARN could not label namespace $NAMESPACE for Pod Security"
fi

failed=0
for app in $APPS; do
  "${ACTION}_app" "$app" || failed=$((failed + 1))
done

if [[ "$ACTION" == "apply" || "$ACTION" == "status" ]]; then
  ips="$(node_ips)"
  [[ -n "$ips" ]] && log "open on any node: ${ips% }  (hpe-enterprise :30081, hello-world :30082)"
fi
if (( failed )); then
  log "RESULT: FAILED ($failed app(s))"
  exit 1
fi
log "RESULT: OK"
