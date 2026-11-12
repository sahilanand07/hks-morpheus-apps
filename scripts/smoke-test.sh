#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# smoke-test.sh - test the deployed apps on the cluster nodes (no kubectl needed).
#
# For each app and each node: GET /healthz and / on the NodePort, check HTTP 200,
# that the page is the right app, and print the pod that answered and the version
# it serves. Then send REQUESTS requests to one node and count the distinct pods,
# which shows the Service spreading traffic across the replicas.
#
# Usage (from vmemgr, a node, or any host that reaches the node IPs):
#   bash scripts/smoke-test.sh                          # lab nodes below
#   bash scripts/smoke-test.sh 192.168.200.49 192.168.200.50
#   EXPECT_VERSION=1.1.0 bash scripts/smoke-test.sh      # after an update: fail on old pages
#   APPS="hello-world:30082" bash scripts/smoke-test.sh  # one app only
#
# The proxy is bypassed (--noproxy '*'): node IPs must be reached directly.
# ------------------------------------------------------------------------------
set -uo pipefail

APPS="${APPS:-hpe-enterprise:30081 hello-world:30082}"
REQUESTS="${REQUESTS:-10}"
EXPECT_VERSION="${EXPECT_VERSION:-}"
if (( $# )); then NODES="$*"; else NODES="${NODES:-192.168.200.43 192.168.200.49 192.168.200.50 192.168.200.51}"; fi

tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
fails=0
fetch() {   # prints the HTTP status, 000 when the node does not answer
  local code
  code="$(curl -s --noproxy '*' -m 5 -o "$tmp" -w '%{http_code}' "$1" 2>/dev/null || true)"
  echo "${code:-000}"
}
field() { sed -n "s/.*id=\"$1\">\([^<]*\)<.*/\1/p" "$tmp" | head -1; }

for entry in $APPS; do
  app="${entry%%:*}"; port="${entry##*:}"
  echo
  echo "== $app  (NodePort $port)"
  printf '  %-17s %-7s %-8s %-40s %s\n' NODE HEALTH PAGE POD VERSION
  for node in $NODES; do
    health="$(fetch "http://$node:$port/healthz")"
    page="$(fetch "http://$node:$port/")"
    pod="-"; version="-"; note=""
    if [[ "$page" == "200" ]]; then
      pod="$(field pod)"; version="$(field version)"
      grep -q "<meta name=\"app\" content=\"$app\">" "$tmp" || note="WRONG APP on this port"
      [[ -n "$EXPECT_VERSION" && "$version" != "$EXPECT_VERSION" ]] && note="expected version $EXPECT_VERSION"
    fi
    if [[ "$health" == "200" && "$page" == "200" && -z "$note" ]]; then status=PASS
    else status=FAIL; fails=$((fails + 1)); fi
    printf '  %-17s %-7s %-8s %-40s %-8s %s %s\n' "$node" "$health" "$page" "${pod:--}" "${version:--}" "$status" "$note"
  done

  first="${NODES%% *}"
  pods=""
  for _ in $(seq 1 "$REQUESTS"); do
    [[ "$(fetch "http://$first:$port/")" == "200" ]] && pods+="$(field pod)"$'\n'
  done
  distinct="$(printf '%s' "$pods" | sed '/^$/d' | sort -u)"
  count="$(printf '%s\n' "$distinct" | sed '/^$/d' | wc -l | tr -d ' ')"
  echo "  load balancing: $REQUESTS requests to $first were served by $count pod(s):"
  printf '%s\n' "$distinct" | sed '/^$/d; s/^/    - /'
  if (( count == 0 )); then fails=$((fails + 1)); fi
done

echo
if (( fails )); then
  echo "$fails check(s) failed. See PLAYBOOK.md > Troubleshooting."
  exit 1
fi
echo "All apps answer on every node."
