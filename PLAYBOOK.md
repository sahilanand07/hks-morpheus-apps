# HKS App Deployment Playbook — HPE Morpheus + Git

Deploy the **HPE Enterprise team page** and **Hello World** from this repository to the HKS hybrid
cluster with HPE Morpheus 9.1. Shared, editable copy of this playbook (with the flow diagram):
https://claude.ai/artifact/Kyq5vqXcd4mLdaR2SDZo3t

## Overview

Morpheus deploys both apps by running one Shell Script task on the HKS master over SSH: the task clones
this repository and applies the specs with kubectl. On Morpheus 9.1 this is the reliable route. The
Kubernetes App Blueprint route also gets the apps onto the cluster, but Morpheus loses track of them
(see *Alternative*). Allow 30 minutes the first time and about 2 minutes per deploy after that.

| App | Spec in Git | Kubernetes objects | Replicas | Open it at |
| --- | --- | --- | --- | --- |
| HPE Enterprise team page | `apps/hpe-enterprise/hpe-enterprise.yaml` | ConfigMap `hpe-enterprise-content`, Deployment and Service `hpe-enterprise` | 2 | `http://<any-node-ip>:30081/` |
| Hello World | `apps/hello-world/hello-world.yaml` | ConfigMap `hello-world-content`, Deployment and Service `hello-world` | 2 | `http://<any-node-ip>:30082/` |

- **Repository:** https://github.com/sahilanand07/hks-morpheus-apps, branch `main`.
- **No image build:** each app is the stock `nginxinc/nginx-unprivileged:1.27-alpine` image with its page and
  nginx config in a ConfigMap. The cluster already pulled this image through the proxy during the kit's validation.
- **Namespace:** `hpe-apps`, created by the task with Pod Security `restricted`. The specs set no namespace themselves.
- **Security:** pods run as user 101 with a read-only root filesystem and all capabilities dropped.
- **Each page shows** the pod that served it, the pod IP, the time and the page version. Refreshing shows the
  Service spreading requests across both pods.
- **NodePort 30080** stays with the hks-hybrid-kit validation app in namespace `hello`.

```
 Users / Catalog ──► Morpheus workflow ──► Shell Script task ──SSH──► HKS master (hpe-ns-17)
 (Phase 2)           (Phase 2)             (Phase 2)                  git clone GitHub (main)
                                                                      scripts/morpheus-deploy.sh apply all
                                                                      kubectl apply ──► namespace hpe-apps
                                                                                         hpe-enterprise :30081
                                                                                         hello-world    :30082
```

The work runs in four phases: code in GitHub, deploy with a Morpheus task, verify, then update.

## Prerequisites

Tick each item before Phase 2.

- [ ] **Cluster healthy.** On vmemgr, `bash hks.sh validate` ends with `Cluster is healthy.`
- [ ] **SSH from Morpheus to the master.** The user and password or key pair you used in the cluster wizard work
  for `ssh <user>@<master-ip>`, and that user is root or has passwordless sudo.
- [ ] **git on the master.** `git --version` works on the master (else `sudo apt-get install -y git`).
- [ ] **Master reaches GitHub.** On the master: `git ls-remote https://github.com/sahilanand07/hks-morpheus-apps.git main`
  prints one line (the proxy comes from `/etc/environment`, set by `hks.sh prep`).
- [ ] **Workers can pull the image.** On a worker: `sudo crictl images | grep nginx-unprivileged` lists
  `1.27-alpine`. If not, `sudo crictl pull docker.io/nginxinc/nginx-unprivileged:1.27-alpine` must succeed.
- [ ] **kubectl on the master** for checks: `sudo -i`, then `export KUBECONFIG=/etc/kubernetes/admin.conf`.
- [ ] **Morpheus rights.** Your role can manage Library Automation (Tasks, Workflows) and, for self-service,
  Catalog Items. The master-tenant admin can.

## Phase 1 — Code in GitHub

The repository is the single source of truth: the Morpheus task clones it on every run, and a GitHub
Actions check runs on every push.

```
hks-morpheus-apps/
├── apps/
│   ├── hpe-enterprise/hpe-enterprise.yaml   ConfigMap + Deployment + Service (NodePort 30081)
│   └── hello-world/hello-world.yaml         ConfigMap + Deployment + Service (NodePort 30082)
├── morpheus/task-script.sh                  ← paste into the Morpheus task (ACTION / APP / REF at the top)
├── scripts/
│   ├── morpheus-deploy.sh                   apply | status | delete with kubectl (what the task runs)
│   ├── smoke-test.sh                        test both apps on every node's NodePort
│   ├── validate.sh                          all offline checks (also what CI runs)
│   ├── check_specs.py                       spec rules
│   └── test-pages.sh                        serve each page with real nginx in Docker and check it
├── cluster/namespace.yaml                   namespace hpe-apps, Pod Security restricted (kubectl route)
├── kustomization.yaml                       kubectl apply -k .  (CI, break-glass)
├── .github/workflows/validate.yml
├── README.md
└── PLAYBOOK.md                              this playbook
```

Four rules keep a spec deployable by both routes. The CI check enforces all of them:

1. One self-contained file per app, named after its folder.
2. No `metadata.namespace`: the task (or the Morpheus App wizard) picks the namespace.
3. No dollar signs, no Morpheus template tags and no `---` except as document separators. Morpheus can run
   specs through its variable templating.
4. When the page changes, bump `app_version` in `index.html` and the `hks-morpheus-apps/version` pod annotation to
   the same value.

## Phase 2 — Deploy with a Morpheus task

One Shell Script task does the whole deploy on the master and reports an honest result: it fails if a
rollout or the page check fails, and its output shows every step.

1. **Create the task.** **Library → Automation → Tasks → + ADD**:

| Field | Value |
| --- | --- |
| NAME | `HKS apps - deploy` |
| TYPE | Shell Script |
| SOURCE | Local |
| CONTENT | the contents of `morpheus/task-script.sh` (below) |
| EXECUTE TARGET | Remote |
| Remote host / PORT | the master's IP (`hpe-ns-17`) / 22 |
| USERNAME, PASSWORD or KEY | the SSH user and password or key pair from the cluster wizard |

```bash
ACTION=apply      # apply | status | delete
APP=all           # all | hpe-enterprise | hello-world
REF=main          # branch or tag to deploy

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
```

2. **Wrap it in a workflow.** **Library → Automation → Workflows → + ADD → Operational Workflow**, NAME
   `HKS apps - deploy`, add the task, save.
3. **Run it.** Open the workflow and **Execute** it with no target (the task names its own host). If your task page
   has its own Execute button, that works too. The output is in the execution history.
4. **Optional: self-service tile.** **Library → Blueprints → Catalog Items → + ADD → Workflow**, pick the workflow.
   Users order it from **Provisioning → Catalog**.
5. **Status and teardown tasks.** Copy the task as `HKS apps - status` (`ACTION=status`) and
   `HKS apps - delete` (`ACTION=delete`).

A good run ends like this (pod names differ):

```
hpe-enterprise: applying version 1.0.0 (commit 1d2cd3b) to namespace hpe-apps
    configmap/hpe-enterprise-content created
    deployment.apps/hpe-enterprise created
    service/hpe-enterprise created
  hpe-enterprise: rollout complete
  hpe-enterprise: http://192.168.200.51:30081/ answered from pod hpe-enterprise-6c6cbdfd88-82gjh (version 1.0.0)
OK   hpe-enterprise
... the same for hello-world ...
open on any node: 192.168.200.43 192.168.200.49 192.168.200.50 192.168.200.51  (hpe-enterprise :30081, hello-world :30082)
RESULT: OK
```

What `scripts/morpheus-deploy.sh` does on each run:

- creates `hpe-apps` if it is missing and labels it Pod Security `restricted`;
- runs `kubectl apply`, so it creates **or** updates, including objects an earlier blueprint deploy left behind;
- waits up to 180 s per app (`WAIT`), then GETs the page through the NodePort and prints the pod and version;
- on failure prints the pods and their events, and exits 1 so Morpheus marks the run failed;
- reads the proxy from `/etc/environment` when the SSH session has none, and re-runs itself with `sudo -n` when the
  SSH user cannot read `/etc/kubernetes/admin.conf`.

Task and workflow field names follow the HPE Morpheus 8.x documentation; check the labels on your 9.1 screens.

## Phase 3 — Verify

The deploy is good when the Morpheus run ends `RESULT: OK`, each Deployment shows 2/2 on different workers,
and the smoke test ends `All apps answer on every node.`

**With kubectl on the master:**

```bash
export KUBECONFIG=/etc/kubernetes/admin.conf     # as root on the master; without it kubectl tries localhost:8080
kubectl -n hpe-apps get deploy,pods,svc -o wide
```

Expect `READY 2/2` for each Deployment, the pods spread over the workers, and Services `80:30081/TCP` and
`80:30082/TCP`. In the lab, Hello World came up as 2/2 on `hpe-ns-19` and `hpe-ns-20`.

**Smoke test** from a clone of the repo on vmemgr or the master (it bypasses the proxy for node IPs):

```bash
bash scripts/smoke-test.sh 192.168.200.43 192.168.200.49 192.168.200.50 192.168.200.51
```

**In a browser:** open `http://<node-ip>:30081/` and `http://<node-ip>:30082/`. Refresh a few times: the pod name
changes between the two replicas.

## Phase 4 — Update, roll out, roll back

You change Git, then run the deploy task again: `kubectl apply` replaces the pods one at a time, so the page stays up.

1. **Change the app in Git.** Edit `apps/hello-world/hello-world.yaml` in the GitHub web editor or a clone. Change
   the page, then set the same new version in both places:

```
<!--# set var="app_version" value="1.1.0" -->      (in index.html)
hks-morpheus-apps/version: "1.1.0"                 (pod template annotation)
```

2. **Push and wait for the green check** on the `validate` workflow. It fails if the two versions differ or a rule
   from Phase 1 is broken.
3. **Run `HKS apps - deploy` again** (or a copy with `APP=hello-world`). The output shows `configured` for the
   changed objects, the rollout, and the new version answering.
4. **Confirm on every node:** `EXPECT_VERSION=1.1.0 APPS="hello-world:30082" bash scripts/smoke-test.sh <node-ips>`

**Roll back** with Git, not with `kubectl rollout undo`. The page lives in the ConfigMap, which `rollout undo`
leaves untouched, so it would restart the pods and keep serving the new page. Instead: `git revert <commit>`,
push, and run the deploy task again.

**Pin releases** by tagging: `git tag v1.1.0 && git push --tags`, then set `REF=v1.1.0` in the task. Every run of
that task deploys exactly that version.

## Teardown

Run `HKS apps - delete` (`ACTION=delete`, `APP=all`). It removes the Deployment, Service and ConfigMap of each app
by label and leaves the namespace. Check with `kubectl -n hpe-apps get all,cm` (only `kube-root-ca.crt` remains),
then optionally `kubectl delete ns hpe-apps`.

Without Morpheus: `kubectl -n hpe-apps delete deploy,svc,cm -l app.kubernetes.io/part-of=hks-morpheus-apps`.
Deleting a Morpheus **App** from the blueprint route does not remove anything (see *Alternative*).

## Alternative — Kubernetes App Blueprint (limited on 9.1)

The blueprint route puts the apps on the cluster, but on Morpheus 9.1 Morpheus cannot map the objects back to the
App: the App shows **Unknown**, a redeploy fails while the objects exist, and deleting the App leaves them running.
Use it to show the blueprint catalog; use the task for anything you update.

1. **Git integration.** **Administration → Integrations → + NEW INTEGRATION → Git Repository**: NAME
   `hks-morpheus-apps`, GIT URL `https://github.com/sahilanand07/hks-morpheus-apps.git`, DEFAULT BRANCH `main`
   (type it), USERNAME and PASSWORD empty for this public repo, ENABLE GIT REPOSITORY CACHING unticked.
2. **Namespace in Morpheus.** **Infrastructure → Clusters → <cluster> → Namespaces → + ADD**, NAME `hpe-apps`.
   The App wizard only offers namespaces Morpheus knows.
3. **Spec Template per app.** **Library → Templates → Spec Templates → + ADD**: NAME `hello-world`, TYPE
   **Kubernetes Spec**, SOURCE **Repository**, the integration and repo above, file path
   `apps/hello-world/hello-world.yaml`, ref `main`. Repeat for `hpe-enterprise`.
4. **Blueprint per app.** **Library → Blueprints → App Blueprints → + ADD**, Type **Kubernetes**, CONFIG TYPE
   **Kubernetes Spec**, SPEC TEMPLATE `hello-world`, COMPLETE.
5. **Deploy.** **Provisioning → Apps → + ADD** → the blueprint → **Setup**: NAME, GROUP (`All Clouds` works),
   **RESOURCE POOL** `<cluster> / hpe-apps` (this one field picks cluster and namespace) → **Configure**: NEXT →
   **Review**: COMPLETE.
6. **Check with kubectl**, not the App status: `kubectl -n hpe-apps get deploy,pods,svc`.

Do **not** use CONFIG TYPE **Git Repository** on 9.1: it reads the repo but applies nothing. Before deploying a
blueprint again, remove its objects:
`kubectl -n hpe-apps delete deploy,svc,cm -l app.kubernetes.io/name=hello-world`.

Morpheus 9.1 issues found in the lab (worth an HPE ticket, with the appliance log):

| Issue | Log signature | Effect |
| --- | --- | --- |
| CONFIG TYPE Git Repository applies nothing | `load spec files error: groovy.lang.MissingMethodException: ... appendSpecFiles() ... (ArrayList, [Ljava.io.File;)` at `KubernetesProvisionService.groovy:6844` | App "succeeds", status Unknown, no objects |
| Objects not linked to the App | `KubernetesResourceMappingService - mapResult: null` | App status Unknown, empty Resources tab, App delete leaves the objects |
| Create-only deploy | `409 ... already exists`, `422 ... nodePort ... provided port is already allocated` | Redeploy fails while the objects exist; no updates |
| Wizard preview crash | `LinkedHashMap cannot be cast to class java.lang.CharSequence` at `KubernetesComputeUtility.groovy:168` | Preview only; the deploy itself continues |

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Task fails at once: SSH or authentication error | Wrong host, user, password or key in the task | Test `ssh <user>@<master-ip>` from vmemgr with the same credentials. |
| Task output: `git clone of 'main' failed` | git missing, or the master can't reach GitHub | `git --version`; `bash hks.sh proxy` on vmemgr; check the ref exists. |
| Task output: `cannot reach the Kubernetes API` | Not running on the master, or the API address is missing from `NO_PROXY` | Point the task at the master; keep the node subnet in `NO_PROXY` (`hks.env`). |
| Task output: `admin.conf needs root and passwordless sudo is not available` | The SSH user has no passwordless sudo | Give it `NOPASSWD` sudo, or use root. |
| Task output: `FAIL <app>: not ready after 180s` | Pods can't start; the task prints their status and events | Read the events: image pull (proxy), Pod Security, scheduling. |
| Task output: `<app>: http://<ip>:<port>/ did not answer yet` | Pods ready but the NodePort not reachable from the master | `curl --noproxy '*' http://<ip>:<port>/healthz` on the master; check kube-proxy and Calico. |
| `kubectl apply` warns `missing the kubectl.kubernetes.io/last-applied-configuration annotation` | The objects were first created by a Morpheus blueprint deploy | Harmless; kubectl adds it on that run. |
| Pods `ErrImagePull` / `ImagePullBackOff` | containerd can't reach Docker Hub through the proxy | On vmemgr `bash hks.sh proxy`; on a worker test `sudo crictl pull docker.io/nginxinc/nginx-unprivileged:1.27-alpine`. |
| Pods stuck `ContainerCreating`, event mentions Calico and `Gateway Timeout` | Pod and service ranges missing from containerd `NO_PROXY` | `bash hks.sh proxy`, then delete the Pending pods (hks-hybrid-kit Troubleshooting). |
| ReplicaSet event `violates PodSecurity "restricted"` | The securityContext was edited away | Restore the spec from Git; the shipped specs pass `restricted`. |
| Page shows `unknown` or raw `<!--#` text | SSI switched off in `default.conf` | Keep `ssi on;` in the `location /` block (CI checks this). |
| `kubectl` says `connection to the server localhost:8080 was refused` | No kubeconfig in that shell | `export KUBECONFIG=/etc/kubernetes/admin.conf` on the master. |
| `curl` to a node IP hangs on vmemgr | The request went to the internet proxy | Use `curl --noproxy '*'`; `smoke-test.sh` already does. |
| Page didn't change after a push | Nothing was rolled out yet | Run the deploy task again (Phase 4). |
| Blueprint App shows Unknown and nothing was deployed | CONFIG TYPE Git Repository (9.1 bug) | Use the task, or the Spec Template blueprint (*Alternative*). |
| Blueprint redeploy: `already exists` / `provided port is already allocated` | Blueprint deploys only create | Use the task, or delete the objects first (*Alternative*). |
| Blueprint CONFIG shows `Failed to load remote git config: undefined` | BRANCH OR TAG left empty (Morpheus then asks for `master`) | Type `main`. |

## Optional extras

- **One task with choices.** Add Morpheus Inputs (for example `hksAction`, `hksApp`) to the workflow or catalog
  item and set `ACTION=<%=customOptions.hksAction%>` and `APP=<%=customOptions.hksApp%>` in the task instead of
  fixed values.
- **kubectl route (no Morpheus).** `kubectl apply -k .` from the repo root creates `hpe-apps` with Pod Security
  `restricted` and both apps; `bash scripts/morpheus-deploy.sh apply all` on the master does the same with checks.
- **Friendly URL on port 80.** `hks-on-hvm/nginx-proxy-setup.sh` fronts one app per run, for example
  `sudo NODEPORT=30081 APP_HOSTNAME=hpe.hpe.lab WORKER_IPS="192.168.200.49 192.168.200.50 192.168.200.51" bash nginx-proxy-setup.sh`.
- **GitOps.** For deploy-on-push without anyone running a task, an Argo CD or Flux controller on the cluster can
  watch this repo; Morpheus would then only install and observe it.

## Reference values

| Item | Value |
| --- | --- |
| Morpheus appliance | `vmemgr.hpe.lab`, HPE Morpheus 9.1 |
| Cluster | `k8sfinal`, cloud `HKS-HYBRID`, Kubernetes v1.35.10, Calico; master `hpe-ns-17`, workers `hpe-ns-18`–`20` |
| Nodes (kit) | `192.168.200.43`, `.49`, `.50`, `.51` (check with `kubectl get nodes -o wide`) |
| Pod / service CIDR | `172.20.0.0/16` / `172.30.0.0/16` |
| Internet proxy | `http://192.168.200.250:888` |
| Namespace | `hpe-apps` |
| NodePorts | 30081 HPE Enterprise page, 30082 Hello World (30080 = kit validation app) |
| Image | `nginxinc/nginx-unprivileged:1.27-alpine` |
| Repository | `https://github.com/sahilanand07/hks-morpheus-apps.git`, branch `main` |

## What was tested

- **Lab (Morpheus 9.1, cluster k8sfinal):** the Spec Template blueprint deployed Hello World (2/2 on `hpe-ns-19`
  and `hpe-ns-20`, NodePort 30082); CONFIG TYPE Git Repository applied nothing; a blueprint redeploy failed with
  `already exists`. The task route has not had its first lab run yet.
- **`morpheus-deploy.sh` and `morpheus/task-script.sh`** against a Kubernetes v1.35.0 API server: apply (namespace
  created and labelled), re-apply over objects created without kubectl as Morpheus does, rollout timeout with
  diagnostics, status, delete, bad input, a missing ref, standalone clone from GitHub as a non-root sudo user, and
  proxy settings taken from an env file.
- **Spec rules:** `check_specs.py` passes both apps and catches deliberate faults (version mismatch, dollar sign,
  template tag, stray `---`, namespace set, duplicate and reserved NodePort, `latest` tag).
- **Schemas:** kubeconform `-strict` against Kubernetes 1.35.0, raw files and kustomize output: 7 of 7 valid.
- **Pod Security:** on a v1.35.0 API server `restricted` admitted all four pods and rejected a non-compliant one.
- **Pages:** each served by the real image as user 101 with a read-only root filesystem; SSI fills pod, IP, time and
  version; rendered in Chrome on desktop and phone widths.

## Sources

- [Tasks — HPE Morpheus Enterprise docs](https://docs.morpheusdata.com/en/latest/library/automation/tasks.html)
- [Kubernetes Blueprints — HPE Morpheus Enterprise docs 8.0.6](https://docs.morpheusdata.com/en/8.0.6/library/blueprints/appBlueprints/kubernetes.html)
- [Git integration — HPE Morpheus Enterprise docs 8.0.5](https://docs.morpheusdata.com/en/8.0.5/integration_guides/Deployments/Git.html)
- [Spec Templates — HPE Morpheus Enterprise docs 8.0.6](https://docs.morpheusdata.com/en/8.0.6/library/templates/specTemplates.html)
