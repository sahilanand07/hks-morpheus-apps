# hks-morpheus-apps

Three demo apps for the HKS (HPE Kubernetes Service) hybrid cluster, deployed from this repository by
**HPE Morpheus 9.1**: a Morpheus Shell Script task runs `scripts/morpheus-deploy.sh` on the master over SSH
(recommended), or a Kubernetes App Blueprint applies the specs (works, with 9.1 limits).

| App | Spec | NodePort | Page |
| --- | --- | --- | --- |
| HPE Enterprise team page | `apps/hpe-enterprise/hpe-enterprise.yaml` | 30081 | "This page is for the HPE Enterprise team." |
| Hello World | `apps/hello-world/hello-world.yaml` | 30082 | "Hello, World!" |
| Private AI Assistant | `apps/doc-assistant/doc-assistant.yaml` | 30083 | Ask questions about documents; a small language model answers on CPUs, with citations |

The two pages show the pod that served them, its IP, the time and the page version, so refreshing demonstrates load
balancing across the 2 replicas. They need no image build: each is `nginxinc/nginx-unprivileged:1.27-alpine` with
its page and nginx config in a ConfigMap.

The **Private AI Assistant** is private AI on plain Kubernetes: IBM Granite 4.1 3B and Granite Embedding run on
Ollama (CPU only) inside the cluster, so no document or question leaves it. It answers from this repo's docs or a
PDF you drop on the page, and cites its sources. GitHub Actions builds its two images from `images/` and pushes
them to `ghcr.io/sahilanand07` (models built into the image: no internet, proxy or volume needed by the pods).

All pods run non-root with a read-only root filesystem and pass Pod Security `restricted`.

**Full step-by-step procedure: [PLAYBOOK.md](PLAYBOOK.md)** (Morpheus task → verify → update/rollback → teardown,
the Private AI Assistant and its demo script, the self-service catalog, the blueprint alternative and its 9.1 limits,
troubleshooting).

## Quick start

**Morpheus route (recommended on Morpheus 9.1): a Shell Script task**

1. *Library → Automation → Tasks → + Add*: type Shell Script, source Local, content = `morpheus/task-script.sh`,
   execute target Remote = the HKS master over SSH (root, or a user with passwordless sudo).
2. *Library → Automation → Workflows → + Add → Operational Workflow* with that task; Execute it.
   Optional: a *Workflow* catalog item for self-service.
3. The run clones this repo on the master, runs `scripts/morpheus-deploy.sh apply all` (kubectl apply, rollout
   wait, page check through the NodePort) and ends with `RESULT: OK`. Run it again after every push to roll out.
   `APP=doc-assistant` deploys only the AI assistant; its first run waits while each node pulls the 2.5 GB model image.

The Kubernetes App Blueprint route also deploys the apps, but on 9.1 Morpheus loses track of them (App status
Unknown, create-only, delete leaves the objects) and CONFIG TYPE *Git Repository* applies nothing. Details and the
log signatures for an HPE ticket are in [PLAYBOOK.md](PLAYBOOK.md) → *Alternative*.

**kubectl route (no Morpheus, CI, break-glass):**

```bash
kubectl apply -k .                 # namespace hpe-apps (restricted) + all three apps
bash scripts/smoke-test.sh         # or: curl http://<node-ip>:30081/  :30082/  :30083/
kubectl delete -k .                # remove again
```

Use one route or the other on a cluster, not both: the NodePorts are fixed and would clash.

## Layout

```
apps/hpe-enterprise/hpe-enterprise.yaml   Morpheus spec: ConfigMap + Deployment + Service
apps/hello-world/hello-world.yaml         Morpheus spec: ConfigMap + Deployment + Service
apps/doc-assistant/doc-assistant.yaml     Morpheus spec: ConfigMap + 2 Deployments + 2 Services (AI assistant)
images/doc-assistant/                     AI assistant web app (FastAPI): Dockerfile, app, tests, knowledge, VERSION
images/slm/                               model server image: Ollama (CPU) + Granite models built in, VERSION
cluster/namespace.yaml                    namespace hpe-apps, Pod Security restricted
kustomization.yaml                        kubectl apply -k .
morpheus/task-script.sh                   Morpheus Shell Script task body (ACTION / APP / REF at the top)
morpheus/catalog-task-script.sh           the same for a self-service catalog item with an order form
scripts/morpheus-deploy.sh                apply | status | delete with kubectl (what the task runs)
scripts/smoke-test.sh                     test every app on every node (no kubectl needed)
scripts/validate.sh                       offline checks: spec rules, kubeconform, nginx + page render, app tests
scripts/check_specs.py                    Morpheus-specific spec rules (used by validate.sh)
scripts/test-pages.sh                     run each page in Docker like the pod does and check the output
.github/workflows/validate.yml            runs validate.sh on every push
.github/workflows/image-*.yml             build and push the AI assistant's images to ghcr.io
```

## Changing a page

1. Edit the HTML inside the ConfigMap in `apps/<app>/<app>.yaml`.
2. Bump `app_version` in the page (`<!--# set var="app_version" value="1.1.0" -->`) **and** the
   `hks-morpheus-apps/version` pod annotation to the same value.
3. Push; the *validate* workflow must pass.
4. Roll out: run the Morpheus deploy task again (rolling update, no downtime), or
   `bash scripts/morpheus-deploy.sh apply <app>` on the master.
5. `EXPECT_VERSION=1.1.0 bash scripts/smoke-test.sh`

Rules the CI enforces, because Morpheus applies these files as they are: one self-contained file per app named after
its folder; no `metadata.namespace`; no dollar signs or Morpheus template tags anywhere in the file; matching
versions (for images built here: image tag = `images/<name>/VERSION` = pod annotation); unique NodePorts that avoid
30080 (used by the hks-hybrid-kit validation app); pinned image tags; Pod Security `restricted` settings.

## Environment

Built for the lab cluster from `hks-hybrid-kit`: Morpheus `vmemgr.hpe.lab`, nodes `192.168.200.43` (master),
`.49`, `.50`, `.51`, proxy `http://192.168.200.250:888`, Kubernetes v1.35.10 with Calico. Air-gapped: mirror the
nginx and `ghcr.io/sahilanand07/hks-*` images to your registry and change the `image:` lines in the specs.
