# HKS App Deployment Playbook — HPE Morpheus + Git

Deploy the **HPE Enterprise team page** and **Hello World** from this repository to the HKS hybrid
cluster with HPE Morpheus 9.1. Shared, editable copy of this playbook (with the flow diagram):
https://claude.ai/artifact/Kyq5vqXcd4mLdaR2SDZo3t

## Overview

Two web apps live in GitHub and HPE Morpheus 9.1 deploys them, as Kubernetes Apps, onto the HKS hybrid
cluster (1 master, 3 workers, Kubernetes v1.35.10). Allow 30–45 minutes the first time and about
5 minutes per app after that.

| App | Spec in Git (Morpheus SPEC PATH / CONFIG) | Kubernetes objects | Replicas | Open it at |
| --- | --- | --- | --- | --- |
| HPE Enterprise team page | `apps/hpe-enterprise` / `hpe-enterprise.yaml` | ConfigMap `hpe-enterprise-content`, Deployment and Service `hpe-enterprise` | 2 | `http://<any-node-ip>:30081/` |
| Hello World | `apps/hello-world` / `hello-world.yaml` | ConfigMap `hello-world-content`, Deployment and Service `hello-world` | 2 | `http://<any-node-ip>:30082/` |

- **Repository:** https://github.com/sahilanand07/hks-morpheus-apps, branch `main`.
- **No image build:** each app is the stock `nginxinc/nginx-unprivileged:1.27-alpine` image with its page and
  nginx config in a ConfigMap. The cluster already pulled this image through the proxy during the kit's validation.
- **Namespace:** `hpe-apps`, chosen in the Morpheus App wizard. The specs set no namespace themselves.
- **Security:** pods run as user 101 with a read-only root filesystem and all capabilities dropped. They pass
  Pod Security `restricted`.
- **Each page shows** the pod that served it, the pod IP, the time and the page version. Refreshing shows the
  Service spreading requests across both pods.
- **NodePort 30080** stays with the hks-hybrid-kit validation app in namespace `hello`.

```
 GitHub repo ──► Morpheus Git integration ──► App Blueprints (x2) ──► Morpheus Apps ──► HKS cluster k8s-1
 (Phase 1)       (Phase 2, branch main)       (Phase 4)               (Phase 5)         namespace hpe-apps
      │                                                                                  hpe-enterprise :30081
      └──────────── Phase 7: kubectl apply (rolling update, no downtime) ──────────────► hello-world    :30082
                                                                                               ▲
                                                     Users: browser, scripts/smoke-test.sh ────┘ (Phase 6)
```

The work runs in seven phases: code in GitHub, Git integration, namespace, blueprints, deploy, verify, then update.

## Prerequisites

Tick each item before Phase 1.

- [ ] **Cluster healthy.** On vmemgr, `bash hks.sh validate` ends with `Cluster is healthy.`
- [ ] **kubectl on the master.** `sudo -i`, then `export KUBECONFIG=/etc/kubernetes/admin.conf`. Or download the
  kubeconfig from Morpheus: cluster **Actions → View Kube Config**.
- [ ] **Appliance reaches GitHub.** **Administration → Settings → Proxy Settings** points at `192.168.200.250:888`
  (kit step 1.2). Test on vmemgr: `curl -sSI -x http://192.168.200.250:888 https://github.com | head -1` prints an
  HTTP 200 or 301 line.
- [ ] **Workers can pull the image.** On a worker: `sudo crictl images | grep nginx-unprivileged` lists
  `1.27-alpine`. If not, `sudo crictl pull docker.io/nginxinc/nginx-unprivileged:1.27-alpine` must succeed.
- [ ] **NodePorts free.** `kubectl get svc -A | grep -E '3008[12]'` prints nothing.
- [ ] **Morpheus rights.** Your role can manage Integrations, Library Blueprints, Provisioning Apps and Clusters.
  The master-tenant admin can.
- [ ] **Names noted.** The cluster's group, its cloud (`HKS-HYBRID`) and the cluster name (`k8s-1` below; use yours).

## Phase 1 — Code in GitHub

The repository is the single source of truth: Morpheus reads each app's spec straight from it, and a GitHub
Actions check runs on every push.

```
hks-morpheus-apps/
├── apps/
│   ├── hpe-enterprise/hpe-enterprise.yaml   ← Morpheus spec: ConfigMap + Deployment + Service (NodePort 30081)
│   └── hello-world/hello-world.yaml         ← Morpheus spec: ConfigMap + Deployment + Service (NodePort 30082)
├── cluster/namespace.yaml                   namespace hpe-apps, Pod Security restricted (kubectl route)
├── kustomization.yaml                       kubectl apply -k .  (CI, break-glass, no-Morpheus route)
├── scripts/
│   ├── smoke-test.sh                        test both apps on every node's NodePort
│   ├── validate.sh                          all offline checks (also what CI runs)
│   ├── check_specs.py                       Morpheus-specific spec rules
│   └── test-pages.sh                        serve each page with real nginx in Docker and check it
├── .github/workflows/validate.yml
├── README.md
└── PLAYBOOK.md                              this playbook
```

Four rules keep a spec deployable by Morpheus. The CI check enforces all of them:

1. One self-contained file per app, named after its folder.
2. No `metadata.namespace`: the namespace is picked when the App is deployed.
3. No dollar signs and no Morpheus template tags anywhere in the file, comments included. Morpheus can run specs
   through its variable templating.
4. When the page changes, bump `app_version` in `index.html` and the `hks-morpheus-apps/version` pod annotation to
   the same value.

To work with the code on vmemgr or the master (the nodes already have the proxy in `/etc/environment`):

```bash
export https_proxy=http://192.168.200.250:888     # vmemgr only, if not already set
git clone https://github.com/sahilanand07/hks-morpheus-apps.git
cd hks-morpheus-apps
bash scripts/validate.sh                          # optional: needs python3-yaml; kubeconform and docker steps skip if missing
```

## Phase 2 — Connect Morpheus to the repository

One Git integration lets every blueprint read the repo; with caching off, each deploy fetches the latest commit.

1. Go to **Administration → Integrations** and click **+ NEW INTEGRATION** (**+ ADD** on older screens). The same
   form is under **Provisioning → Code → Integrations**.
2. Choose **Git Repository** and fill in:

| Field | Value | Note |
| --- | --- | --- |
| NAME | `hks-morpheus-apps` | |
| ENABLED | ticked | |
| GIT URL | `https://github.com/sahilanand07/hks-morpheus-apps.git` | |
| DEFAULT BRANCH | `main` | Type it; don't rely on a master/main default. |
| USERNAME / PASSWORD | empty | Public repo. For a private repo: GitHub user name, and a personal access token in PASSWORD. |
| ACCESS TOKEN | empty | Unused; tokens go in PASSWORD. |
| KEY PAIR | empty | Only for SSH URLs. |
| ENABLE GIT REPOSITORY CACHING | unticked | Fresh fetch on every use. Ticked reuses a copy up to 5 minutes old. |

3. Click **SAVE CHANGES**, then open the integration. It should list the repository and the `main` branch. An
   error here is almost always the appliance proxy: see Troubleshooting.

## Phase 3 — Create the namespace

Create `hpe-apps` from Morpheus so the App wizard offers it, then turn on Pod Security `restricted` with one
kubectl command.

1. **Infrastructure → Clusters → k8s-1 → Namespaces** tab → **+ ADD**.
2. NAME `hpe-apps`, leave it active, and save.
3. On the master, enforce Pod Security (recommended; both apps were tested against it):

```bash
kubectl label ns hpe-apps pod-security.kubernetes.io/enforce=restricted pod-security.kubernetes.io/warn=restricted
```

If you created the namespace with kubectl instead (`kubectl apply -f cluster/namespace.yaml`), run the cluster's
**Actions → Refresh** so Morpheus picks it up. If it still doesn't appear in the wizard, create it in Morpheus as above.

## Phase 4 — Create the two App Blueprints

Each app gets a Kubernetes App Blueprint whose config comes straight from its file in Git.

1. Go to **Library → Blueprints → App Blueprints** and click **+ ADD**.
2. On the **Name** tab, enter the NAME from the table and set **Type** to **Kubernetes**. Click **NEXT**.
3. In **Cluster Summary**, enter NAME, DESCRIPTION and CATEGORY `web`.
4. In **Kubernetes Configuration**, set the Git fields from the table.
5. Click **COMPLETE**. Repeat for the second app.

| Field | HPE Enterprise team page | Hello World |
| --- | --- | --- |
| NAME | `HPE Enterprise Page` | `Hello World` |
| DESCRIPTION | HPE Enterprise team landing page, NodePort 30081 | Hello World demo, NodePort 30082 |
| CONFIG TYPE | Git Repository | Git Repository |
| SCM INTEGRATION | your Git integration from Phase 2 | same |
| REPOSITORY | `hks-morpheus-apps` | `hks-morpheus-apps` |
| BRANCH OR TAG | `main` (type it; empty means `master`) | `main` |
| SPEC PATH | `apps/hpe-enterprise` | `apps/hello-world` |
| CONFIG | `hpe-enterprise.yaml` | `hello-world.yaml` |

Type `main` in BRANCH OR TAG. Left empty, Morpheus 9.1 looks for `master`, which this repo doesn't have, and CONFIG
shows `Failed to load remote git config: undefined`. SPEC PATH (WORKING PATH in the 8.x docs) is the app's folder,
with no `./` at the start and no slash at the end; CONFIG then lists the file. A tag such as `v1.0.0` instead of
`main` pins a release (Phase 7).

## Phase 5 — Deploy the Apps

Deploy each blueprint once into `hpe-apps` on `k8s-1`; each App is Running within about 2 minutes.

1. Go to **Provisioning → Apps** and click **+ ADD**.
2. **Template:** select the blueprint `HPE Enterprise Page` and click **NEXT**.
3. **Setup:** NAME `hpe-enterprise`; GROUP = a group that contains the cluster's cloud (`All Clouds` works);
   **RESOURCE POOL** = `<cluster> / hpe-apps` (for example `k8sfinal / hpe-apps`): this one field picks both cluster
   and namespace; ENVIRONMENT and LABELS optional. Click **NEXT**.
4. **Configure:** leave the blueprint's item as it is and click **NEXT**. If it asks for a cluster or namespace
   again, pick the same ones.
5. **Review:** click **COMPLETE**.
6. Watch the App's status turn **Running**; its **History** tab shows the apply.
7. Repeat with blueprint `Hello World`, App NAME `hello-world`.

The App NAME is only the Morpheus record: the Kubernetes objects keep the names in the YAML (`hpe-enterprise`,
`hello-world`). Steps 1–3 match the 9.1 wizard (Template, Setup, Configure, Review) seen in the lab.

Deploy each blueprint only once. The NodePorts are fixed, so a second copy, or the kubectl route on top of a
Morpheus App, fails with `provided port is already allocated`.

## Phase 6 — Verify

The deploy is good when both Apps are Running in Morpheus, each Deployment shows 2/2 on different workers, and the
smoke test ends `All apps answer on every node.`

**In Morpheus:** both Apps show **Running**. **Infrastructure → Clusters → k8s-1** lists the two Deployments, four
pods and two Services in `hpe-apps`.

**With kubectl on the master:**

```bash
kubectl -n hpe-apps get deploy,pods,svc -o wide
kubectl -n hpe-apps rollout status deploy/hpe-enterprise
kubectl -n hpe-apps rollout status deploy/hello-world
```

Expect `READY 2/2` for each Deployment, the pods spread over the workers, and Services `80:30081/TCP` and
`80:30082/TCP`.

**Smoke test** from the repo clone on vmemgr or the master (it bypasses the proxy for node IPs):

```bash
bash scripts/smoke-test.sh                       # lab nodes .43 .49 .50 .51
bash scripts/smoke-test.sh 192.168.200.49        # or just some nodes
```

Example of a passing run (pod names will differ):

```
== hpe-enterprise  (NodePort 30081)
  NODE              HEALTH  PAGE     POD                                      VERSION
  192.168.200.43    200     200      hpe-enterprise-6c6cbdfd88-82gjh          1.0.0    PASS
  192.168.200.49    200     200      hpe-enterprise-6c6cbdfd88-wck5m          1.0.0    PASS
  ...
  load balancing: 10 requests to 192.168.200.43 were served by 2 pod(s):
    - hpe-enterprise-6c6cbdfd88-82gjh
    - hpe-enterprise-6c6cbdfd88-wck5m
...
All apps answer on every node.
```

**In a browser:** open `http://192.168.200.49:30081/` and `http://192.168.200.49:30082/`. Refresh a few times: the
pod name changes between the two replicas.

## Phase 7 — Update, roll out, roll back

A push alone changes nothing on the cluster: you change Git, then roll the new spec out, either with kubectl (no
downtime) or by redeploying the Morpheus App (about a minute of downtime).

**1. Change the app in Git.** Edit `apps/hello-world/hello-world.yaml` in the GitHub web editor or a clone. Change
the page, then set the same new version in both places:

```
<!--# set var="app_version" value="1.1.0" -->      (in index.html)
hks-morpheus-apps/version: "1.1.0"                 (pod template annotation)
```

**2. Push and wait for the green check** on the `validate` workflow. It fails if the two versions differ or a rule
from Phase 1 is broken.

**3. Roll it out — choose one:**

| Option | How | Downtime | Use when |
| --- | --- | --- | --- |
| A. Rolling update (recommended) | On the master, in the clone: `git pull`, then `kubectl -n hpe-apps apply -f apps/hello-world/hello-world.yaml` and `kubectl -n hpe-apps rollout status deploy/hello-world` | None: a new pod starts before an old one stops | Day-to-day changes, live demos |
| B. Morpheus redeploy | **Provisioning → Apps → hello-world → DELETE**, check `kubectl -n hpe-apps get svc hello-world` is gone, then deploy the blueprint again (Phase 5) | About 1 minute | Showing the full Morpheus flow, or after renaming objects |

**4. Confirm the new version is live** on every node:

```bash
EXPECT_VERSION=1.1.0 APPS="hello-world:30082" bash scripts/smoke-test.sh
```

**Roll back** with Git, not with `kubectl rollout undo`. The page lives in the ConfigMap, which `rollout undo`
leaves untouched, so it would restart the pods and keep serving the new page. Instead: `git revert <commit>`,
push, and roll out again with A or B.

**Pin releases** by tagging: `git tag v1.1.0 && git push --tags`, then set the blueprint's BRANCH OR TAG to the tag.
A Morpheus redeploy then always gives that exact version.

## Teardown

Remove what Morpheus deployed through Morpheus, then confirm with kubectl that nothing is left behind.

1. **Provisioning → Apps** → select `hpe-enterprise` → **DELETE**. If the dialog offers to remove the associated
   resources, tick it. Repeat for `hello-world`.
2. Check: `kubectl -n hpe-apps get all,cm` shows only `kube-root-ca.crt`. Delete any leftover by name, for example
   `kubectl -n hpe-apps delete svc hello-world`.
3. Optional: delete the namespace (cluster **Namespaces** tab, or `kubectl delete ns hpe-apps`). The blueprints and
   the Git integration can stay for the next demo.

If you used the kubectl route instead, `kubectl delete -k .` from the repo root removes both apps and the namespace.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Git integration shows an error, or no repository or branch | Appliance can't reach github.com | Set **Administration → Settings → Proxy Settings** to `192.168.200.250:888`; test with the `curl -x` line from Prerequisites. |
| Blueprint CONFIG shows `Failed to load remote git config: undefined` or lists nothing | BRANCH OR TAG left empty (Morpheus then asks for `master`), or SPEC PATH wrong | `apps/hpe-enterprise` or `apps/hello-world`, no `./` or trailing slash; type BRANCH OR TAG `main`. |
| Morpheus reads `master` instead of `main` | Default branch not honoured | Set DEFAULT BRANCH `main` on the integration and BRANCH OR TAG `main` on the blueprint. |
| `hpe-apps` missing from the App wizard | Namespace created with kubectl only | Create it in Morpheus (Phase 3), or run the cluster's **Actions → Refresh**. |
| Deploy fails: `provided port is already allocated` | The app already runs (an earlier App, the kubectl route, or a leftover Service) | `kubectl get svc -A \| grep 3008`; delete the old App or the leftover Service, then deploy again. |
| Pods `ErrImagePull` / `ImagePullBackOff` | containerd can't reach Docker Hub through the proxy | On vmemgr `bash hks.sh proxy`; on a worker test `sudo crictl pull docker.io/nginxinc/nginx-unprivileged:1.27-alpine`. |
| Pods stuck `ContainerCreating`, event mentions Calico and `Gateway Timeout` | Pod and service ranges missing from containerd `NO_PROXY` | `bash hks.sh proxy`, then delete the Pending pods (hks-hybrid-kit Troubleshooting). |
| ReplicaSet event `violates PodSecurity "restricted"` | The securityContext was edited away | Restore the spec from Git; the shipped specs pass `restricted`. |
| Pods `Pending` | No room or a taint on the workers | `kubectl -n hpe-apps describe pod <pod>` and read Events. |
| Page shows `unknown` or raw `<!--#` text | SSI switched off in `default.conf` | Keep `ssi on;` in the `location /` block (CI checks this). |
| `curl` to a node IP hangs on vmemgr | The request went to the internet proxy | Use `curl --noproxy '*'`; `smoke-test.sh` already does. |
| Page didn't change after a push | Nothing was rolled out yet | Phase 7 step 3. With repository caching ticked, Morpheus may also use a copy up to 5 minutes old. |
| `kubectl apply` warns `missing the kubectl.kubernetes.io/last-applied-configuration annotation` | Morpheus created the objects without that annotation | Harmless the first time; kubectl adds it. |

## Optional extras

- **Self-service tile.** **Library → Blueprints → Catalog Items → + ADD**, type Blueprint, select an App Blueprint
  and preset group, cloud, cluster and namespace. Users then order it from **Provisioning → Catalog**.
- **kubectl route (no Morpheus).** `kubectl apply -k .` from the repo root creates `hpe-apps` with Pod Security
  `restricted` and both apps. Don't mix it with Morpheus Apps on the same cluster: the NodePorts clash.
- **Spec Template route.** **Library → Templates → Spec Templates → + ADD**, TYPE Kubernetes Spec, SOURCE Repository,
  pointing at the same file. A blueprint can then use CONFIG TYPE Kubernetes Spec, and the template can be reused
  elsewhere in the Library.
- **Friendly URL on port 80.** `hks-on-hvm/nginx-proxy-setup.sh` fronts one app per run, for example
  `sudo NODEPORT=30081 APP_HOSTNAME=hpe.hpe.lab WORKER_IPS="192.168.200.49 192.168.200.50 192.168.200.51" bash nginx-proxy-setup.sh`.
- **Morpheus API.** `POST /api/clusters/{id}/apply-template` takes `serviceUrl`, `specYaml` or `specTemplate` but no
  namespace, so these namespace-less specs would land in the default namespace. Use the App route above instead.

## Reference values

| Item | Value |
| --- | --- |
| Morpheus appliance | `vmemgr.hpe.lab`, HPE Morpheus 9.1 |
| Cluster | `k8s-1` (your name), cloud `HKS-HYBRID`, Kubernetes v1.35.10, Calico |
| Nodes | master `192.168.200.43`; workers `.49`, `.50`, `.51` |
| Pod / service CIDR | `172.20.0.0/16` / `172.30.0.0/16` |
| Internet proxy | `http://192.168.200.250:888` |
| Namespace | `hpe-apps` |
| NodePorts | 30081 HPE Enterprise page, 30082 Hello World (30080 = kit validation app) |
| Image | `nginxinc/nginx-unprivileged:1.27-alpine` |
| Repository | `https://github.com/sahilanand07/hks-morpheus-apps.git`, branch `main` |

## What was tested

- **Spec rules:** `check_specs.py` passes both apps and catches seven deliberate faults (version mismatch, dollar
  sign, template tag, namespace set, duplicate and reserved NodePort, `latest` tag).
- **Schemas:** kubeconform `-strict` against Kubernetes 1.35.0, raw files and kustomize output: 7 of 7 valid.
- **Real API server:** applied to a Kubernetes v1.35.0 API server with controller manager. Pod Security
  `restricted` admitted all four pods and rejected a non-compliant one. A version bump created a new ReplicaSet
  (rolling update). A second copy failed with `provided port is already allocated`.
- **Pages:** each served by the real image as user 101 with a read-only root filesystem. `/healthz` returns ok,
  SSI fills pod, IP, time and version, `Cache-Control: no-store` is set, and the nginx version is hidden.
  Rendered in Chrome on desktop and phone widths.
- **Smoke test:** run against simulated nodes, including a dead node and a wrong version.
- **Not yet run:** the Morpheus 9.1 screens themselves and the live HKS cluster. Phases 2–5 follow HPE Morpheus 8.x
  documentation; the 9.1 blueprint form (SPEC PATH, branch default master) was checked against a lab screenshot.

## Sources

- [Kubernetes Blueprints — HPE Morpheus Enterprise docs 8.0.6](https://docs.morpheusdata.com/en/8.0.6/library/blueprints/appBlueprints/kubernetes.html)
- [Git integration — HPE Morpheus Enterprise docs 8.0.5](https://docs.morpheusdata.com/en/8.0.5/integration_guides/Deployments/Git.html)
- [Spec Templates — HPE Morpheus Enterprise docs 8.0.6](https://docs.morpheusdata.com/en/8.0.6/library/templates/specTemplates.html)
- [Apply Template to Cluster (Kubernetes) — Morpheus API reference](https://apidocs.morpheusdata.com/reference/applytemplate)
