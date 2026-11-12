#!/usr/bin/env python3
"""Static checks for the Morpheus Kubernetes specs in apps/<app>/<app>.yaml.

Checks that matter for the Morpheus route (and that kubeconform cannot see):
  * one file per app, named after its folder (SPEC PATH / CONFIG in the blueprint)
  * exactly one ConfigMap, Deployment and Service; nothing sets metadata.namespace
    (the namespace is picked in the Morpheus App wizard)
  * no dollar signs and no Morpheus template tags in the file
  * app_version in index.html == hks-morpheus-apps/version pod annotation
  * selectors, ConfigMap references and NodePorts are consistent; NodePorts are
    unique and avoid 30080 (used by the hks-hybrid-kit validation app)

Usage:
  python3 scripts/check_specs.py                 # run the checks
  python3 scripts/check_specs.py --extract DIR   # also write DIR/<app>/default.conf
                                                 # and DIR/<app>/html/index.html
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
TEMPLATE_TAG = "<" + "%"          # written this way so this file passes its own check
VERSION_RE = re.compile(r'<!--#\s*set\s+var="app_version"\s+value="([^"]+)"\s*-->')

failures = []


def ok(msg):
    print(f"  PASS  {msg}")


def fail(msg):
    print(f"  FAIL  {msg}")
    failures.append(msg)


def check_app(path, nodeports, extract_dir):
    app = path.parent.name
    print(f"\n{path.relative_to(ROOT)}")
    text = path.read_text(encoding="utf-8")

    if path.stem == app:
        ok(f"file name matches folder (Morpheus SPEC PATH apps/{app}, CONFIG {path.name})")
    else:
        fail(f"{path.name}: name it {app}.yaml so the blueprint settings in the playbook apply")

    if "$" in text:
        line = text[: text.index("$")].count("\n") + 1
        fail(f"dollar sign on line {line}: keep it out of Morpheus specs")
    elif TEMPLATE_TAG in text:
        line = text[: text.index(TEMPLATE_TAG)].count("\n") + 1
        fail(f"Morpheus template tag on line {line}")
    else:
        ok("no dollar signs or Morpheus template tags")

    try:
        docs = [d for d in yaml.safe_load_all(text) if d]
    except yaml.YAMLError as exc:
        fail(f"YAML does not parse: {exc}")
        return

    by_kind = {}
    for d in docs:
        by_kind.setdefault(d.get("kind"), []).append(d)
        if "namespace" in (d.get("metadata") or {}):
            fail(f"{d['kind']}/{d['metadata'].get('name')} sets metadata.namespace; "
                 "remove it, Morpheus chooses the namespace at deploy time")
    if sorted(by_kind) == ["ConfigMap", "Deployment", "Service"] and all(len(v) == 1 for v in by_kind.values()):
        ok("exactly one ConfigMap, Deployment and Service")
    else:
        fail(f"expected one ConfigMap, Deployment and Service, found {[d.get('kind') for d in docs]}")
        return

    cm, dep, svc = by_kind["ConfigMap"][0], by_kind["Deployment"][0], by_kind["Service"][0]
    data = cm.get("data") or {}
    if {"default.conf", "index.html"} <= set(data):
        ok(f"ConfigMap {cm['metadata']['name']} holds default.conf and index.html")
    else:
        fail(f"ConfigMap {cm['metadata']['name']} needs keys default.conf and index.html")
        return

    html = data["index.html"]
    m = VERSION_RE.search(html)
    pod_tpl = dep["spec"]["template"]
    annotated = (pod_tpl.get("metadata", {}).get("annotations") or {}).get(VERSION_ANNOTATION)
    if not m:
        fail('index.html has no <!--# set var="app_version" value="..." --> line')
    elif str(annotated) != m.group(1):
        fail(f"version mismatch: index.html app_version {m.group(1)} vs "
             f"{VERSION_ANNOTATION} annotation {annotated}; bump both")
    else:
        ok(f"version {m.group(1)} in index.html matches the pod annotation")

    if "ssi on;" in data["default.conf"] and "listen 8080;" in data["default.conf"]:
        ok("nginx config listens on 8080 with SSI on")
    else:
        fail("default.conf must keep 'listen 8080;' and 'ssi on;' (the page uses SSI)")

    selector = dep["spec"]["selector"]["matchLabels"]
    tpl_labels = pod_tpl["metadata"]["labels"]
    if all(tpl_labels.get(k) == v for k, v in selector.items()):
        ok("Deployment selector matches the pod labels")
    else:
        fail("Deployment selector does not match the pod template labels")
    if all(tpl_labels.get(k) == v for k, v in (svc["spec"].get("selector") or {}).items()):
        ok("Service selector matches the pod labels")
    else:
        fail("Service selector does not match the pod template labels")

    cm_refs = {v["configMap"]["name"] for v in pod_tpl["spec"].get("volumes", []) if "configMap" in v}
    if cm_refs == {cm["metadata"]["name"]}:
        ok("volumes use this file's ConfigMap")
    else:
        fail(f"volumes reference {sorted(cm_refs)}, expected {cm['metadata']['name']}")

    for c in pod_tpl["spec"]["containers"]:
        image = c.get("image", "")
        if ":" not in image.rsplit("/", 1)[-1] or image.endswith(":latest"):
            fail(f"container {c['name']}: pin the image tag ({image})")
        else:
            ok(f"image pinned: {image}")

    if svc["spec"].get("type") != "NodePort":
        fail("Service type must be NodePort (the playbook tests on node IPs)")
    for p in svc["spec"].get("ports", []):
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
            ok(f"NodePort {np} is unique")

    if extract_dir:
        out = pathlib.Path(extract_dir) / app
        (out / "html").mkdir(parents=True, exist_ok=True)
        (out / "default.conf").write_text(data["default.conf"], encoding="utf-8")
        (out / "html" / "index.html").write_text(html, encoding="utf-8")
        version_file = out / "version"
        version_file.write_text((m.group(1) if m else "") + "\n", encoding="utf-8")
        nodeport_file = out / "nodeport"
        nodeport_file.write_text(f"{svc['spec']['ports'][0].get('nodePort', '')}\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--extract", metavar="DIR", help="write each app's nginx config and page to DIR/<app>/")
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
