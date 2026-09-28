#!/usr/bin/env python3
"""Re-root a built fixture under a URL prefix, to serve it below an origin's root.

build.py writes a site for an origin root: every reference is root-absolute
(`/p/p0380.html`, `/img/hero.png`), which is how nginx serves it to the guests.
The public copy at https://evan.mx/fleetkit-fixture/ lives under a prefix, so
this copies the tree and rewrites every reference in the pages, in app.js (the
card template build.py injects) and in catalog.json and catalog.js, then checks
that every reference in the copy resolves to a file and refuses to leave a copy
in which one does not. Nothing else changes: the images, styles.css,
favicon.svg and manifest.json are copied as they are. The manifest keeps
describing the root-served build, which is the one the harness measures.

    python3 fixture/reroot.py --src fixture/dist --out /tmp/fixture-public --prefix /fleetkit-fixture
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

# Attributes that carry a URL in the pages and in the templates inside app.js. In app.js the
# templates are JSON strings, so the quote may be escaped.
ATTR_REF = re.compile(r'\b(href|src|action|data-src)=(\\?")/')
# Any attribute whose value is root-absolute, for the check: one the rewrite missed is an error.
ANY_ROOT_REF = re.compile(r'\b([a-z-]+)=\\?"(/[^"\\]*)')
CATALOG_REF = re.compile(r'"/img/')
REWRITTEN_TEXT = ("index.html", "search.html", "cart.html", "app.js")
CATALOGS = ("catalog.json", "catalog.js")


class RerootError(Exception):
    pass


def normalise(prefix: str) -> str:
    """'/fleetkit-fixture/', 'fleetkit-fixture' and '/fleetkit-fixture' all mean '/fleetkit-fixture'."""
    p = "/" + prefix.strip("/")
    if p == "/":
        raise RerootError("the prefix must name a path below the root, such as /fleetkit-fixture")
    if re.search(r"[^A-Za-z0-9._~/-]", p):
        raise RerootError("the prefix may only contain letters, digits, '-', '_', '.', '~' and '/': " + p)
    return p


def rewrite_text(text: str, prefix: str) -> str:
    return ATTR_REF.sub(lambda m: "{}={}{}/".format(m.group(1), m.group(2), prefix), text)


def rewrite_catalog(text: str, prefix: str) -> str:
    return CATALOG_REF.sub('"{}/img/'.format(prefix), text)


def reroot(src: Path, out: Path, prefix: str) -> int:
    """Copy src to out with every reference under prefix. Returns the number of references checked."""
    prefix = normalise(prefix)
    if not (src / "manifest.json").is_file():
        raise RerootError("{} is not a fixture build (no manifest.json); run fixture/build.py first".format(src))
    if out.exists():
        raise RerootError("{} exists; refusing to overwrite".format(out))
    shutil.copytree(src, out)
    for path in list(out.glob("*.html")) + list((out / "p").glob("*.html")) + [out / "app.js"]:
        path.write_text(rewrite_text(path.read_text(encoding="utf-8"), prefix), encoding="utf-8")
    for name in CATALOGS:
        path = out / name
        path.write_text(rewrite_catalog(path.read_text(encoding="utf-8"), prefix), encoding="utf-8")
    return check(out, prefix)


def check(out: Path, prefix: str) -> int:
    """Every root-absolute reference in the copy is under prefix and names a file. Returns the count."""
    prefix = normalise(prefix)
    problems: list[str] = []
    count = 0

    def resolve(ref: str, where: str) -> None:
        nonlocal count
        count += 1
        if not ref.startswith(prefix + "/"):
            problems.append("{}: {} is not under {}".format(where, ref, prefix))
            return
        rel = ref[len(prefix) + 1:].split("?", 1)[0].split("#", 1)[0] or "index.html"
        if rel.endswith("/"):
            rel += "index.html"
        if not (out / rel).is_file():
            problems.append("{}: {} does not resolve to a file".format(where, ref))

    for path in list(out.glob("*.html")) + list((out / "p").glob("*.html")) + [out / "app.js"]:
        text = path.read_text(encoding="utf-8")
        for m in ANY_ROOT_REF.finditer(text):
            if "{" in m.group(2):  # a template placeholder, filled by app.js at run time
                continue
            resolve(m.group(2), str(path.relative_to(out)))
    catalog = json.loads((out / "catalog.json").read_text(encoding="utf-8"))
    for p in catalog["products"]:
        for ref in [p["image"]] + list(p.get("gallery", [])):
            resolve(ref, "catalog.json " + p["id"])
    catalog_js = (out / "catalog.js").read_text(encoding="utf-8")
    if "window.FLEETKIT_CATALOG=" not in catalog_js or json.dumps(catalog, separators=(",", ":"), sort_keys=True) not in catalog_js:
        problems.append("catalog.js does not carry the same catalog as catalog.json")
    if problems:
        raise RerootError("{} problem(s) in {}:\n  ".format(len(problems), out) + "\n  ".join(problems[:20]))
    return count


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--src", type=Path, default=Path(__file__).resolve().parent / "dist", help="a fixture build (default: fixture/dist)")
    ap.add_argument("--out", type=Path, required=True, help="where to write the copy (must not exist)")
    ap.add_argument("--prefix", default="/fleetkit-fixture", help="the URL prefix the copy is served under (default: /fleetkit-fixture)")
    args = ap.parse_args()
    try:
        n = reroot(args.src, args.out, args.prefix)
    except RerootError as e:
        print("reroot: " + str(e), file=sys.stderr)
        return 1
    print("reroot: {} references under {} in {}".format(n, normalise(args.prefix), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
