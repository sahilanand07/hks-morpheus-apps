# hks-morpheus-apps

Two demo web apps for the HKS (HPE Kubernetes Service) hybrid cluster, deployed from this repository by
**HPE Morpheus 9.1** through a Git integration and Kubernetes App Blueprints.

| App | Morpheus spec (WORKING PATH / CONFIG) | NodePort | Page |
| --- | --- | --- | --- |
| HPE Enterprise team page | `apps/hpe-enterprise` / `hpe-enterprise.yaml` | 30081 | "This page is for the HPE Enterprise team." |
| Hello World | `apps/hello-world` / `hello-world.yaml` | 30082 | "Hello, World!" |

Each page shows the pod that served it, its IP, the time and the page version, so refreshing demonstrates load
balancing across the 2 replicas. No image build or registry is needed: each app is
`nginxinc/nginx-unprivileged:1.27-alpine` with its page and nginx config in a ConfigMap. Pods run non-root with a
read-only root filesystem and pass Pod Security `restricted`.

**Full step-by-step procedure: [PLAYBOOK.md](PLAYBOOK.md)** (Git integration → namespace → blueprints → deploy →
verify → update/rollback → teardown → troubleshooting).

## Quick start

**Morpheus route (the demo):**

1. *Administration → Integrations → + New Integration → Git Repository*: GIT URL
   `https://github.com/sahilanand07/hks-morpheus-apps.git`, DEFAULT BRANCH `main`, caching off.
2. Create namespace `hpe-apps` on the cluster (*Infrastructure → Clusters → k8s-1 → Namespaces*).
3. *Library → Blueprints → App Blueprints → + Add*, type Kubernetes, CONFIG TYPE *Git Repository*, WORKING PATH and
   CONFIG from the table above. One blueprint per app.
4. *Provisioning → Apps → + Add*, pick the blueprint, cloud `HKS-HYBRID`, cluster `k8s-1`, namespace `hpe-apps`.
5. `bash scripts/smoke-test.sh` → `All apps answer on every node.`

**kubectl route (no Morpheus, CI, break-glass):**

```bash
kubectl apply -k .                 # namespace hpe-apps (restricted) + both apps
bash scripts/smoke-test.sh         # or: curl http://<node-ip>:30081/  and  :30082/
kubectl delete -k .                # remove again
```

Use one route or the other on a cluster, not both: the NodePorts are fixed and would clash.

## Layout

```
apps/hpe-enterprise/hpe-enterprise.yaml   Morpheus spec: ConfigMap + Deployment + Service
apps/hello-world/hello-world.yaml         Morpheus spec: ConfigMap + Deployment + Service
cluster/namespace.yaml                    namespace hpe-apps, Pod Security restricted
kustomization.yaml                        kubectl apply -k .
scripts/smoke-test.sh                     test both apps on every node (no kubectl needed)
scripts/validate.sh                       offline checks: spec rules, kubeconform, nginx + page render
scripts/check_specs.py                    Morpheus-specific spec rules (used by validate.sh)
scripts/test-pages.sh                     run each page in Docker like the pod does and check the output
.github/workflows/validate.yml            runs validate.sh on every push
```

## Changing a page

1. Edit the HTML inside the ConfigMap in `apps/<app>/<app>.yaml`.
2. Bump `app_version` in the page (`<!--# set var="app_version" value="1.1.0" -->`) **and** the
   `hks-morpheus-apps/version` pod annotation to the same value.
3. Push; the *validate* workflow must pass.
4. Roll out: `kubectl -n hpe-apps apply -f apps/<app>/<app>.yaml` (no downtime), or redeploy the Morpheus App.
5. `EXPECT_VERSION=1.1.0 bash scripts/smoke-test.sh`

Rules the CI enforces, because Morpheus applies these files as they are: one self-contained file per app named after
its folder; no `metadata.namespace`; no dollar signs or Morpheus template tags anywhere in the file; matching
versions; unique NodePorts that avoid 30080 (used by the hks-hybrid-kit validation app); pinned image tag.

## Environment

Built for the lab cluster from `hks-hybrid-kit`: Morpheus `vmemgr.hpe.lab`, nodes `192.168.200.43` (master),
`.49`, `.50`, `.51`, proxy `http://192.168.200.250:888`, Kubernetes v1.35.10 with Calico. Air-gapped: mirror the
nginx image to your registry and change the `image:` line in both specs.
