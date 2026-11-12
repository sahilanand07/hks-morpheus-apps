#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# test-pages.sh - run each app's nginx config and page in Docker, the same way the
# pod runs them (image, non-root user 101, read-only root, /tmp in memory, all
# capabilities dropped), then check what nginx actually serves:
#   /healthz -> 200 "ok", / -> 200 with the SSI fields filled in (pod name, IP,
#   time, version) and no unprocessed SSI or SSI error text.
#
# Usage: bash scripts/test-pages.sh          (needs docker, python3 + PyYAML, curl)
# ------------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="${IMAGE:-nginxinc/nginx-unprivileged:1.27-alpine}"
work="$(mktemp -d)"
containers=()
cleanup() {
  for c in "${containers[@]}"; do docker rm -f "$c" >/dev/null 2>&1 || true; done
  rm -rf "$work"
}
trap cleanup EXIT

python3 scripts/check_specs.py --extract "$work" >/dev/null || true
chmod -R a+rX "$work"

fails=0
pass() { printf '  PASS  %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; fails=$((fails + 1)); }

for dir in "$work"/*/; do
  app="$(basename "$dir")"
  version="$(cat "$dir/version")"
  name="test-${app}-$$"
  echo
  echo "$app (expects version $version)"

  docker run -d --name "$name" --hostname "${app}-test-pod" \
    --user 101:101 --read-only --tmpfs /tmp:rw,size=16m \
    --cap-drop ALL --security-opt no-new-privileges \
    -v "$dir/default.conf:/etc/nginx/conf.d/default.conf:ro" \
    -v "$dir/html:/usr/share/nginx/html:ro" \
    "$IMAGE" >/dev/null
  containers+=("$name")

  ip=""
  for _ in $(seq 1 20); do
    ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$name" 2>/dev/null || true)"
    if [[ -n "$ip" ]] && curl -s --noproxy '*' -m 2 -o /dev/null "http://$ip:8080/healthz"; then break; fi
    sleep 0.5
  done

  if [[ "$(docker inspect -f '{{.State.Running}}' "$name")" != "true" ]]; then
    fail "nginx did not start"; docker logs "$name" 2>&1 | tail -5 | sed 's/^/        /'; continue
  fi
  pass "nginx starts as user 101 with a read-only root filesystem"

  health="$(curl -s --noproxy '*' -m 5 "http://$ip:8080/healthz" || true)"
  if [[ "$health" == "ok" ]]; then pass "/healthz -> ok"; else fail "/healthz returned '$health'"; fi

  code="$(curl -s --noproxy '*' -m 5 -o "$work/$app.out" -w '%{http_code}' "http://$ip:8080/" || true)"
  if [[ "$code" == "200" ]]; then pass "/ -> HTTP 200"; else fail "/ -> HTTP $code"; fi
  body="$work/$app.out"

  if grep -q "<meta name=\"app\" content=\"$app\">" "$body"; then pass "page identifies itself as $app"
  else fail "page is missing <meta name=\"app\" content=\"$app\">"; fi
  pod="$(sed -n 's/.*id="pod">\([^<]*\)<.*/\1/p' "$body" | head -1)"
  if [[ "$pod" == "${app}-test-pod" ]]; then pass "pod name filled in by SSI: $pod"; else fail "pod name is '$pod'"; fi
  podip="$(sed -n 's/.*id="ip">\([^<]*\)<.*/\1/p' "$body" | head -1)"
  if [[ "$podip" == "$ip" ]]; then pass "pod IP filled in by SSI: $podip"; else fail "pod IP is '$podip', expected $ip"; fi
  served="$(sed -n 's/.*id="version">\([^<]*\)<.*/\1/p' "$body" | head -1)"
  if [[ "$served" == "$version" ]]; then pass "version shown: $served"; else fail "version shown is '$served'"; fi
  if grep -q '<!--#' "$body"; then fail "unprocessed SSI directive in the output"; else pass "no unprocessed SSI directives"; fi
  if grep -qi 'an error occurred' "$body"; then fail "SSI error text in the output"; else pass "no SSI errors"; fi
  headers="$(curl -s --noproxy '*' -m 5 -D - -o /dev/null "http://$ip:8080/" || true)"
  if grep -qi '^cache-control: no-store' <<<"$headers"; then pass "Cache-Control: no-store (each refresh hits a pod)"
  else fail "Cache-Control: no-store header missing"; fi
  if grep -qiE '^server: nginx/[0-9]' <<<"$headers"; then fail "nginx version exposed"; else pass "nginx version hidden"; fi
done

echo
if (( fails )); then echo "$fails page check(s) failed."; exit 1; fi
echo "All page checks passed."
