# ==============================================================================
# Morpheus Shell Script task body - paste this into the task's CONTENT field.
#   Library > Automation > Tasks > + ADD > Shell Script, SOURCE Local,
#   EXECUTE TARGET Remote = the HKS master over SSH (user with passwordless sudo, or root).
#
# One task per action is simplest: copy the task and change ACTION / APP / REF.
#   ACTION  apply | status | delete
#   APP     all | hpe-enterprise | hello-world | doc-assistant (Private AI Assistant)
#   REF     branch or tag of the repo to deploy (main, or a release tag such as v1.1.0)
# The task fails (non-zero exit) when the deploy or the check fails.
# doc-assistant: the first deploy waits up to 25 min while each node pulls the 2.5 GB model image.
# For a self-service catalog item with choices, use morpheus/catalog-task-script.sh instead.
# ==============================================================================
ACTION=apply
APP=all
REF=main

# Non-login SSH sessions may not load the proxy settings: take them from /etc/environment.
set -a
# shellcheck disable=SC1091
[ -r /etc/environment ] && . /etc/environment
set +a

d=$(mktemp -d)
git clone -q --depth 1 --branch "$REF" https://github.com/sahilanand07/hks-morpheus-apps.git "$d/repo" \
  || { echo "git clone of '$REF' failed: check git, the proxy in /etc/environment and the ref"; rm -rf "$d"; exit 1; }
rc=0
REF="$REF" bash "$d/repo/scripts/morpheus-deploy.sh" "$ACTION" "$APP" || rc=$?
rm -rf "$d"
exit $rc
