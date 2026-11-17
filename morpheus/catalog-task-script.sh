# ==============================================================================
# Morpheus Shell Script task body for a SELF-SERVICE CATALOG ITEM with choices.
# Same as task-script.sh, but ACTION / APP / REF come from the catalog order form.
#
# Create three Inputs (Library > Options > Inputs), then add them to the catalog item:
#   Field Name  hksApp     Type Select List  values: all, hpe-enterprise, hello-world, doc-assistant
#   Field Name  hksAction  Type Select List  values: apply, status, delete
#   Field Name  hksRef     Type Text         default: main
# Task:  Library > Automation > Tasks > + ADD > Shell Script, SOURCE Local, this file as
#        CONTENT, EXECUTE TARGET Remote = the HKS master over SSH.
# Then:  Operational Workflow with this task > Catalog Item (type Workflow) with the inputs.
# Anything unexpected in the form falls back to a safe default (apply / all / main).
# ==============================================================================
# The values arrive through quoted here-documents: whatever a user types is read as
# text and never run as shell code.
ACTION=$(cat <<'HKS_INPUT'
<%=customOptions.hksAction%>
HKS_INPUT
)
APP=$(cat <<'HKS_INPUT'
<%=customOptions.hksApp%>
HKS_INPUT
)
REF=$(cat <<'HKS_INPUT'
<%=customOptions.hksRef%>
HKS_INPUT
)

case "$ACTION" in apply|status|delete) ;; *) ACTION=apply ;; esac
case "$APP" in all|hpe-enterprise|hello-world|doc-assistant) ;; *) APP=all ;; esac
case "$REF" in ""|null|*[!A-Za-z0-9._/-]*) REF=main ;; esac
echo "catalog order: ACTION=$ACTION APP=$APP REF=$REF"

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
