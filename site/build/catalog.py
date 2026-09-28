"""site/catalog.json: the only list of what the site publishes.

A campaign is read from results/<id>/: its campaign.json (the definition as launched) and one directory per
run, <spec>-r<k>. A campaign published before campaigns existed (cap-baseline-1) names its one run directory
under ``before_campaigns`` instead. Nothing else under results/ is read, and results/dev never is.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

FORMAT = "fleetkit-catalog/2"
ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
RESERVED = {"compare"}  # #/results/compare is the compare page (DATA.md, Routes)
RUN_DIR = re.compile(r"^([a-z][a-z0-9]*(?:-[a-z0-9]+)*)-r([1-9]\d*)$")


class CatalogError(Exception):
    pass


def _inside_results(repo: Path, rel: str, what: str) -> Path:
    p = (repo / rel).resolve()
    results = (repo / "results").resolve()
    if results not in p.parents:
        raise CatalogError(f"{what}: {rel} is outside results/")
    if p == results / "dev" or (results / "dev") in p.parents:
        raise CatalogError(f"{what}: {rel} is under results/dev, which is never published (D38)")
    if not p.exists():
        raise CatalogError(f"{what}: {rel} doesn't exist (results/ is local; download it first)")
    return p


def run_dirs(campaign_dir: Path) -> list[tuple[str, int, Path]]:
    """(spec, replica, directory) for every run directory of a campaign, sorted."""
    out = []
    for p in sorted(campaign_dir.iterdir()):
        m = RUN_DIR.match(p.name)
        if p.is_dir() and m and (p / "run.json").exists():
            out.append((m.group(1), int(m.group(2)), p))
    return out


def load(path: Path, repo: Path) -> dict:
    cat = json.loads(path.read_text(encoding="utf-8"))
    if cat.get("format") != FORMAT:
        raise CatalogError(f"{path}: format should be {FORMAT}")
    seen = set()
    for c in cat.get("campaigns") or []:
        cid = c.get("id", "")
        w = f"campaign {cid!r}"
        if not ID.match(cid) or cid in seen:
            raise CatalogError(f"{w}: ids are unique and [a-z0-9-]")
        if cid in RESERVED:
            raise CatalogError(f"{w}: {cid!r} is reserved by the site's routes (#/results/{cid})")
        if cid.endswith("-synthetic") or re.match(r"Synthetic\b", str(c.get("title", ""))):
            raise CatalogError(f"{w}: synthetic campaigns are never published")
        seen.add(cid)
        if not c.get("title"):
            raise CatalogError(f"{w}: needs a title")
        old = c.get("before_campaigns")
        if old:
            if not c.get("question") or not ID.match(str(old.get("spec", ""))):
                raise CatalogError(f"{w}: a campaign of one needs a question and its spec's name")
            c["_runs"] = [(old["spec"], 1, _inside_results(repo, old["dir"], f"{w} run"))]
            c["_env_file"] = _inside_results(repo, old["env_file"], f"{w} env_file") if old.get("env_file") else None
        else:
            cdir = _inside_results(repo, f"results/{cid}", w)
            definition = cdir / "campaign.json"
            if not definition.exists():
                raise CatalogError(f"{w}: results/{cid}/campaign.json is missing")
            c["_definition"] = definition
            c["_runs"] = run_dirs(cdir)
            c["_env_file"] = None
        for n in c.get("notes") or []:
            if not isinstance(n, str) or not n.strip():
                raise CatalogError(f"{w}: notes are plain sentences")
    featured = cat.get("featured")
    if featured is not None and featured not in seen:
        raise CatalogError(f"featured: {featured} isn't in the catalog")
    for a in cat.get("gate_allow") or []:
        if not a.get("text") or not a.get("reason"):
            raise CatalogError("gate_allow entries need the exact text and a reason")
    return cat
