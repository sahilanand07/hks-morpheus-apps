#!/usr/bin/env python3
"""Static checks for the Morpheus Kubernetes specs in apps/<app>/<app>.yaml.

Checks that matter for the Morpheus route (and that kubeconform cannot see):
  * one file per app, named after its folder (SPEC PATH in the blueprint / task)
  * only ConfigMaps, Deployments and Services; nothing sets metadata.namespace
    (the namespace is picked at deploy time)
  * every object carries app.kubernetes.io/name=<app> and part-of=hks-morpheus-apps
    (morpheus-deploy.sh deletes by these labels)
  * no dollar signs and no Morpheus template tags in the file
  * '---' only as a document separator line
  * exactly one NodePort Service, named after the app; other Services are ClusterIP
  * selectors, ConfigMap references and NodePorts are consistent; NodePorts are
    unique and avoid 30080 (used by the hks-hybrid-kit validation app)
  * every pod passes Pod Security "restricted" (non-root, seccomp, no privilege
    escalation, all capabilities dropped) and pins its image tags
  * versions: nginx pages - app_version in index.html == the pod annotation;
    images built here (ghcr.io/<owner>/hks-<name>) - tag == images/<name>/VERSION
    == the pod's hks-morpheus-apps/version annotation

Usage:
  python3 scripts/check_specs.py                 # run the checks
  python3 scripts/check_specs.py --extract DIR   # also write DIR/<app>/default.conf,
                                                 # html/index.html, version, nodeport (nginx apps)
Needs PyYAML (python3-yaml / pip install pyyaml).
"""
import argparse
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESERVED_NODEPORTS = {30080: "hks-hybrid-kit validation app (namespace hello)"}
VERSION_ANNOTATION = "hks-morpheus-apps/version"
PART_OF = ("app.kubernetes.io/part-of", "hks-morpheus-apps")
TEMPLATE_TAG = "<" + "%"          # written this way so this file passes its own check
VERSION_RE = re.compile(r'<!--#\s*set\s+var="app_version"\s+value="([^"]+)"\s*-->')
OWN_IMAGE_RE = re.compile(r"^ghcr\.io/[^/]+/hks-([a-z0-9-]+):(.+)$")
ALLOWED_KINDS = {"ConfigMap", "Deployment", "Service"}

failures = []


def ok(msg):
    print(f"  PASS  {msg}")


def fail(msg):
    print(f"  FAIL  {msg}")
    failures.append(msg)


def name_of(d):
    return f"{d.get('kind')}/{(d.get('metadata') or {}).get('name')}"


def check_text(path, app, text):
    if path.stem == app:
        ok(f"file name matches folder (SPEC PATH apps/{app}/{path.name})")
    else:
        fail(f"{path.name}: name it {app}.yaml so the blueprint and task settings in the playbook apply")

    if "$" in text:
        line = text[: text.index("$")].count("\n") + 1
        fail(f"dollar sign on line {line}: keep it out of Morpheus specs")
    elif TEMPLATE_TAG in text:
        line = text[: text.index(TEMPLATE_TAG)].count("\n") + 1
        fail(f"Morpheus template tag on line {line}")
    else:
        ok("no dollar signs or Morpheus template tags")

    stray = [i for i, line in enumerate(text.splitlines(), 1) if "---" in line and line.strip() != "---"]
    if stray:
        fail(f"'---' outside a document separator on line(s) {stray}: a naive split on '---' would cut the file there")
    else:
        ok("'---' appears only as document separators")


def check_objects(app, docs):
    bad = False
    for d in docs:
        meta = d.get("metadata") or {}
        labels = meta.get("labels") or {}
        if d.get("kind") not in ALLOWED_KINDS:
            fail(f"{name_of(d)}: only ConfigMap, Deployment and Service are expected here")
            bad = True
        if "namespace" in meta:
            fail(f"{name_of(d)} sets metadata.namespace; remove it, the namespace is chosen at deploy time")
            bad = True
        if labels.get("app.kubernetes.io/name") != app or labels.get(PART_OF[0]) != PART_OF[1]:
            fail(f"{name_of(d)} needs labels app.kubernetes.io/name: {app} and {PART_OF[0]}: {PART_OF[1]}")
            bad = True
    if not bad:
        ok(f"{len(docs)} objects, all labelled app.kubernetes.io/name={app}, no namespace set")


def check_pod_security(dep):
    spec = dep["spec"]["template"]["spec"]
    psc = spec.get("securityContext") or {}
    problems = []
    if psc.get("runAsNonRoot") is not True:
        problems.append("pod securityContext.runAsNonRoot: true")
    if (psc.get("seccompProfile") or {}).get("type") not in ("RuntimeDefault", "Localhost"):
        problems.append("pod securityContext.seccompProfile.type: RuntimeDefault")
    for c in spec.get("containers", []) + spec.get("initContainers", []):
        sc = c.get("securityContext") or {}
        if sc.get("allowPrivilegeEscalation") is not False:
            problems.append(f"{c['name']}: allowPrivilegeEscalation: false")
        if "ALL" not in ((sc.get("capabilities") or {}).get("drop") or []):
            problems.append(f"{c['name']}: capabilities.drop [ALL]")
        if sc.get("privileged"):
            problems.append(f"{c['name']}: not privileged")
    if problems:
        fail(f"{name_of(dep)} breaks Pod Security 'restricted' (namespace label): needs " + "; ".join(problems))
    else:
        ok(f"{name_of(dep)} passes Pod Security 'restricted'")


def check_images(dep):
    pod_tpl = dep["spec"]["template"]
    annotated = (pod_tpl.get("metadata", {}).get("annotations") or {}).get(VERSION_ANNOTATION)
    if annotated is None:
        fail(f"{name_of(dep)}: pod template needs the {VERSION_ANNOTATION} annotation")
    for c in pod_tpl["spec"]["containers"]:
        image = c.get("image", "")
        if ":" not in image.rsplit("/", 1)[-1] or image.endswith(":latest"):
            fail(f"container {c['name']}: pin the image tag ({image})")
            continue
        ok(f"image pinned: {image}")
        m = OWN_IMAGE_RE.match(image)
        if not m:
            continue
        name, tag = m.groups()
        vfile = ROOT / "images" / name / "VERSION"
        if not vfile.exists():
            fail(f"{image}: no images/{name}/VERSION in this repo")
            continue
        built = vfile.read_text().strip()
        if tag != built:
            fail(f"{image}: tag {tag} but images/{name}/VERSION says {built}; CI pushes {built}")
        elif str(annotated) != tag:
            fail(f"{name_of(dep)}: {VERSION_ANNOTATION} is {annotated} but the image tag is {tag}; bump both")
        else:
            ok(f"hks-{name} {tag} matches images/{name}/VERSION and the pod annotation")


def check_nginx_page(cm, deps, extract_dir, app, nodeport):
    data = cm.get("data") or {}
    if not {"default.conf", "index.html"} <= set(data):
        fail(f"ConfigMap {cm['metadata']['name']} needs keys default.conf and index.html")
        return
    ok(f"ConfigMap {cm['metadata']['name']} holds default.conf and index.html")
    html = data["index.html"]
    m = VERSION_RE.search(html)
    users = [d for d in deps if any((v.get("configMap") or {}).get("name") == cm["metadata"]["name"]
                                    for v in d["spec"]["template"]["spec"].get("volumes", []))]
    annotated = {(d["spec"]["template"].get("metadata", {}).get("annotations") or {}).get(VERSION_ANNOTATION)
                 for d in users}
    if not m:
        fail('index.html has no <!--# set var="app_version" value="..." --> line')
    elif annotated != {m.group(1)}:
        fail(f"version mismatch: index.html app_version {m.group(1)} vs {VERSION_ANNOTATION} "
             f"annotation {sorted(map(str, annotated))}; bump both")
    else:
        ok(f"version {m.group(1)} in index.html matches the pod annotation")
    if "ssi on;" in data["default.conf"] and "listen 8080;" in data["default.conf"]:
        ok("nginx config listens on 8080 with SSI on")
    else:
        fail("default.conf must keep 'listen 8080;' and 'ssi on;' (the page uses SSI)")
    if extract_dir:
        out = pathlib.Path(extract_dir) / app
        (out / "html").mkdir(parents=True, exist_ok=True)
        (out / "default.conf").write_text(data["default.conf"], encoding="utf-8")
        (out / "html" / "index.html").write_text(html, encoding="utf-8")
        (out / "version").write_text((m.group(1) if m else "") + "\n", encoding="utf-8")
        (out / "nodeport").write_text(f"{nodeport or ''}\n", encoding="utf-8")


def check_app(path, nodeports, extract_dir):
    app = path.parent.name
    print(f"\n{path.relative_to(ROOT)}")
    text = path.read_text(encoding="utf-8")
    check_text(path, app, text)
    try:
        docs = [d for d in yaml.safe_load_all(text) if d]
    except yaml.YAMLError as exc:
        fail(f"YAML does not parse: {exc}")
        return
    check_objects(app, docs)

    cms = {d["metadata"]["name"]: d for d in docs if d.get("kind") == "ConfigMap"}
    deps = [d for d in docs if d.get("kind") == "Deployment"]
    svcs = [d for d in docs if d.get("kind") == "Service"]
    if not deps:
        fail("no Deployment in the file")
        return

    for dep in deps:
        pod_tpl = dep["spec"]["template"]
        tpl_labels = pod_tpl["metadata"]["labels"]
        if all(tpl_labels.get(k) == v for k, v in dep["spec"]["selector"]["matchLabels"].items()):
            ok(f"{name_of(dep)}: selector matches the pod labels")
        else:
            fail(f"{name_of(dep)}: selector does not match the pod template labels")
        spec = pod_tpl["spec"]
        refs = {v["configMap"]["name"] for v in spec.get("volumes", []) if "configMap" in v}
        for c in spec["containers"]:
            refs |= {e["configMapRef"]["name"] for e in c.get("envFrom", []) if "configMapRef" in e}
        missing = refs - set(cms)
        if missing:
            fail(f"{name_of(dep)} uses ConfigMap(s) {sorted(missing)} that this file does not define")
        elif refs:
            ok(f"{name_of(dep)} uses this file's ConfigMap(s) {sorted(refs)}")
        check_pod_security(dep)
        check_images(dep)

    for svc in svcs:
        sel = svc["spec"].get("selector") or {}
        if any(all(d["spec"]["template"]["metadata"]["labels"].get(k) == v for k, v in sel.items()) for d in deps):
            ok(f"{name_of(svc)}: selector matches a Deployment's pods")
        else:
            fail(f"{name_of(svc)}: selector {sel} matches no Deployment in this file")

    front = [s for s in svcs if s["spec"].get("type") == "NodePort"]
    nodeport = None
    if len(front) != 1 or front[0]["metadata"]["name"] != app:
        fail(f"expected exactly one NodePort Service named {app} (the playbook tests on node IPs), "
             f"found {[name_of(s) for s in front]}")
    else:
        for p in front[0]["spec"].get("ports", []):
            np = p.get("nodePort")
            if np is None:
                fail("Service port has no fixed nodePort")
            elif not 30000 <= np <= 32767:
                fail(f"nodePort {np} is outside 30000-32767")
            elif np in RESERVED_NODEPORTS:
                fail(f"nodePort {np} is used by the {RESERVED_NODEPORTS[np]}")
            elif np in nodeports:
                fail(f"nodePort {np} is also used by {nodeports[np]}")
            else:
                nodeports[np] = app
                nodeport = nodeport or np
                ok(f"NodePort {np} is unique")
    for s in svcs:
        if s not in front and s["spec"].get("type", "ClusterIP") != "ClusterIP":
            fail(f"{name_of(s)}: internal Services should be ClusterIP")

    for cm in cms.values():
        if "index.html" in (cm.get("data") or {}) or "default.conf" in (cm.get("data") or {}):
            check_nginx_page(cm, deps, extract_dir, app, nodeport)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--extract", metavar="DIR", help="write each nginx app's config and page to DIR/<app>/")
    args = ap.parse_args()

    specs = sorted(ROOT.glob("apps/*/*.yaml"))
    if not specs:
        print("no specs found under apps/*/*.yaml")
        return 1
    nodeports = {}
    for path in specs:
        check_app(path, nodeports, args.extract)

    print()
    if failures:
        print(f"{len(failures)} check(s) failed.")
        return 1
    print(f"All checks passed for {len(specs)} app(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
