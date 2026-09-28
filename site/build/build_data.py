#!/usr/bin/env python3
"""The dataset builder: reads the campaigns listed in site/catalog.json from results/ and writes the site's
dataset to site/public/data, exactly as site/DATA.md describes.

    harness/.venv/bin/pip install -r site/build/requirements.txt     # once: Pillow
    harness/.venv/bin/python site/build/build_data.py                # writes site/public/data

It builds into a staging directory, then runs four checks, and replaces site/public/data only if all pass:
the scrub gate (no ids, ARNs, addresses, URLs or image metadata), retired words, the size budget, and the
site's own contract checks (site/src/lib/contract.ts, through vitest). A document that breaks a rule is
refused, not repaired.
"""
from __future__ import annotations

import argparse
from functools import cache
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import assemble as A  # noqa: E402
import catalog as C  # noqa: E402
import rules as R  # noqa: E402
import sizes  # noqa: E402
import source as S  # noqa: E402
import specs as SP  # noqa: E402
from images import Images  # noqa: E402
from scrub import Scrubber, gate  # noqa: E402
from words import retired_words_in_json  # noqa: E402

SITE = HERE.parent
REPO = SITE.parent


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def first_commit(repo: Path, path: str) -> str | None:
    """The commit that first held ``path`` (a pre-registration)."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "log", "--diff-filter=A", "--format=%H", "--", path],
                             capture_output=True, text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return None
    return out[-1] if out else None


# The site links github.com/evandbrown/fleetkit (D73): files on its main branch, and the harness at a commit. What
# GitHub has is judged from this clone's remote-tracking branches, as of its last fetch, so a file or commit that
# exists only here (never pushed) is never linked: the link would be broken.
PUBLIC_BRANCH = "origin/main"


def git(repo: Path, *args: str) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None


@cache
def on_github(repo: Path, commit: str) -> bool:
    """Whether GitHub has ``commit``: it's on one of origin's branches."""
    return bool((git(repo, "branch", "-r", "--list", "origin/*", "--contains", commit) or "").strip())


def on_main(repo: Path, rel: str) -> str | None:
    """The file ``rel`` as GitHub's main branch has it; None when it isn't there."""
    return git(repo, "show", f"{PUBLIC_BRANCH}:{rel}")


def definition_path(c: dict, definition: dict, preregistration: dict | None, repo: Path) -> str | None:
    """Where the campaign's definition is public on GitHub's main branch (D73): experiments/campaigns/<id>.json, if
    it's there and is the definition as launched. A reconstructed definition (cap-baseline-1) has no file of its own,
    so its published pre-registration stands in: the document that fixed its spec and criteria first. None when
    neither is."""
    if c.get("before_campaigns"):
        pre = (preregistration or {}).get("path")
        if pre and on_main(repo, pre) is not None:
            return pre
        log(f"  note: {pre or 'no pre-registration'} isn't on GitHub's main branch; no definition linked")
        return None
    rel = f"experiments/campaigns/{c['id']}.json"
    text = on_main(repo, rel)
    if text is None:
        log(f"  note: {rel} isn't on GitHub's main branch (push it, then rebuild); no definition linked")
        return None
    if json.loads(text) != definition:
        log(f"  note: {rel} on GitHub's main branch differs from the definition as launched; no definition linked")
        return None
    return rel


def unpushed_commits_unlinked(built: list[dict], repo: Path) -> None:
    """A run's harness commit is linked only if GitHub has it (D73); otherwise the run links none."""
    for b in built:
        commit = b["entry"]["harness_commit"]
        if commit and not on_github(repo, commit):
            log(f"  note: {b['entry']['id']}: harness commit {commit[:7]} isn't on any of origin's branches "
                "(push it, then rebuild); none linked")
            b["entry"]["harness_commit"] = b["doc"]["harness_commit"] = None


def reconstructed_definition(c: dict, recorded: dict) -> dict:
    """A campaign of one, from before campaigns existed: its definition rebuilt from what its run recorded.
    It has no shutdown timer: that run had none recorded."""
    missing = [p for p in (f["path"] for f in SP.FIELDS) if p not in SP.expand.flatten(recorded)]
    if missing:
        raise A.BuildError(f"{c['id']}: the run didn't record {', '.join(missing)}, so its spec can't be rebuilt")
    return {"name": c["id"], "question": c["question"], "replicas": 1, "base": recorded,
            "specs": {c["before_campaigns"]["spec"]: {}}}


def outcome(spec_name: str, entries: list[dict]) -> dict:
    """A spec's results across its replicas (DATA.md, SpecOutcome)."""
    reps = []
    for e in sorted((e for e in entries if e["spec"] == spec_name), key=lambda e: e["replica"]):
        r = e["result"]
        cost = r["cost_per_1000_tasks"]
        reps.append({"run": e["id"], "host_vcpus": e["host"]["vcpus"],
                     "tested_successfully": r["tested_successfully"], "first_failed": r["first_failed"],
                     "stopped_early": e["stopped_early"], "per_host_vcpu": r["per_host_vcpu"],
                     "midpoint_per_host_vcpu": r["midpoint_per_host_vcpu"],
                     "cost_per_1000_tasks": {"execution": cost["execution"], "observed": cost["observed"]} if cost else None})
    mid = R.mean_midpoint([x["midpoint_per_host_vcpu"] for x in reps])
    return {"spec": spec_name, "replicas": reps, "midpoint_per_host_vcpu": R.r4(mid) if mid is not None else None}


def build_campaign(c: dict, images: Images, repo: Path) -> tuple[dict, dict, list]:
    cid = c["id"]
    log(f"campaign {cid}")
    sources = {}
    env = {}
    for spec_name, replica, run_dir in c["_runs"]:
        src, env = S.read_run(run_dir, SP.TYPES, c.get("_env_file"))
        if src.dropped:
            log(f"  {spec_name}-r{replica}: not published (not part of the experiment): {dict(src.dropped)}")
        sources[(spec_name, replica)] = src

    old = c.get("before_campaigns")
    if old:
        (spec_name, _), src = next(iter(sources.items()))
        definition = reconstructed_definition(c, SP.recorded_spec(src.run, src.manifest, env))
        resolved = SP.resolve(definition, whole=False)
    else:
        definition = json.loads(c["_definition"].read_text(encoding="utf-8"))
        if definition.get("name") != cid:
            raise A.BuildError(f"{cid}: results/{cid}/campaign.json is named {definition.get('name')!r}")
        resolved = SP.resolve(definition)
    base = definition["base"]
    labels = SP.labels(resolved)
    why = definition.get("why") or {}
    specs = [SP.spec_doc(name, labels[name], why.get(name), base, spec) for name, spec in resolved]
    by_name = {s["name"]: s for s in specs}
    unknown = sorted({name for name, _ in sources} - set(by_name))
    if unknown:
        raise A.BuildError(f"{cid}: run directories of specs the definition doesn't have: {unknown}")

    built = []
    order = [s["name"] for s in specs]
    for (spec_name, replica), src in sorted(sources.items(), key=lambda kv: (order.index(kv[0][0]), kv[0][1])):
        if replica > definition["replicas"]:
            raise A.BuildError(f"{cid}: {spec_name} replica {replica} is beyond the definition's {definition['replicas']}")
        sd = by_name[spec_name]
        run_id = f"{spec_name}-r{replica}"
        SP.check_run_matches_spec(run_id, SP.recorded_spec(src.run, src.manifest, env if old else {}), sd["spec"])
        b = A.build_run(src, campaign=cid, run_id=run_id, spec_name=spec_name, replica=replica, spec=sd["spec"],
                        host_of=sd["host"], images=images, log=log)
        if b["entry"]["host"]["instance_type"] != sd["host"]["instance_type"]:
            raise A.BuildError(f"{run_id}: ran on {b['entry']['host']['instance_type']}, its spec says {sd['host']['instance_type']}")
        r = b["entry"]["result"]
        log(f"  {run_id}: {len(b['summaries'])} trials; passed {r['tested_successfully']}, first failed {r['first_failed']}"
            + (", stopped early" if b["entry"]["stopped_early"] else ""))
        built.append(b)

    unpushed_commits_unlinked(built, REPO)       # links are checked against this clone, whatever --repo reads
    rules = built[0]["rules"] if built else []
    for b in built[1:]:
        if b["rules"] != rules:
            raise A.BuildError(f"{cid}: {b['entry']['id']} was judged by different attribution thresholds")
    entries = [b["entry"] for b in built]
    outcomes = [outcome(s["name"], entries) for s in specs]
    starts = [b["source"].run["started_ts"] for b in built]
    ends = [b["source"].run["ended_ts"] for b in built]
    complete = len(entries) == len(specs) * definition["replicas"] and not any(e["stopped_early"] for e in entries)
    status = "complete" if complete else "partial"

    doc = {"schema": A.SCHEMA, "id": cid, "title": c["title"], "question": definition["question"],
           "started": A.minute(min(starts)), "ended": A.minute(max(ends)), "status": status}
    if old:
        doc["before_campaigns"] = True
        doc["reconstructed"] = True
    if c.get("preregistration"):
        commit = first_commit(repo, c["preregistration"])
        if commit:
            doc["preregistration"] = {"path": c["preregistration"], "commit": commit}
        else:
            log(f"  note: no commit found for {c['preregistration']}; preregistration left out")
    doc["definition_path"] = definition_path(c, definition, doc.get("preregistration"), REPO)
    doc.update({"definition": definition, "specs": specs, "runs": entries, "outcomes": outcomes, "rules": rules})
    if c.get("notes"):
        doc["notes"] = list(c["notes"])
    entry = {"id": cid, "title": c["title"], "question": definition["question"], "started": doc["started"],
             "status": status}
    if old:
        entry["before_campaigns"] = True
    entry.update({"replicas": definition["replicas"], "specs": [{"name": s["name"], "label": s["label"]} for s in specs],
                  "runs": len(entries), "outcomes": outcomes})
    return doc, entry, built


def featured(cat: dict, entries: list[dict]) -> str | None:
    """The campaign the site opens on (D58): the catalog's choice, else the newest complete one, else the newest."""
    if cat.get("featured"):
        return cat["featured"]
    complete = [e for e in entries if e["status"] == "complete"]
    return (complete or entries or [{"id": None}])[0]["id"]


def write_json(path: Path, doc, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, ensure_ascii=False, allow_nan=False, indent=2 if pretty else None,
                      separators=None if pretty else (",", ":"))
    path.write_text(text + "\n", encoding="utf-8")


def contract_check(data_dir: Path) -> tuple[bool, str]:
    """Runs site/src/lib/contract.ts over the dataset (build/contract.test.ts, through vitest)."""
    if not (SITE / "node_modules").exists():
        return False, "site/node_modules is missing; run npm ci in site/"
    env = dict(os.environ, FLEETKIT_DATA=str(data_dir))
    p = subprocess.run(["npx", "vitest", "run", "--config", "build/vitest.config.ts"], cwd=SITE, env=env,
                       capture_output=True, text=True)
    return p.returncode == 0, (p.stdout + p.stderr)[-4000:]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--catalog", type=Path, default=SITE / "catalog.json")
    ap.add_argument("--out", type=Path, default=SITE / "public/data")
    ap.add_argument("--repo", type=Path, default=REPO)
    ap.add_argument("--no-contract", action="store_true", help="skip the site's contract check (needs node)")
    args = ap.parse_args(argv)

    try:
        cat = C.load(args.catalog, args.repo)
    except C.CatalogError as exc:
        log(f"refused; site/public/data is unchanged:\n  {exc}")
        return 1
    images = Images()
    scrub = Scrubber()
    docs = {}           # relative path -> (document, pretty)
    entries = []
    for c in cat["campaigns"]:
        try:
            doc, entry, built = build_campaign(c, images, args.repo)
        except (S.SourceError, SP.SpecError, A.BuildError) as exc:
            log(f"refused; site/public/data is unchanged:\n  {exc}")
            return 1
        entries.append(entry)
        docs[f"campaigns/{c['id']}/campaign.json"] = (doc, True)
        for b in built:
            rid = b["entry"]["id"]
            docs[f"campaigns/{c['id']}/runs/{rid}.json"] = (b["doc"], False)
            for t in b["trials"]:
                docs[f"campaigns/{c['id']}/runs/{rid}/{t['id']}.json"] = (t, False)
    entries.sort(key=lambda e: e["started"], reverse=True)
    docs["index.json"] = ({"schema": A.SCHEMA, "featured": featured(cat, entries), "campaigns": entries}, True)

    staging = HERE / ".staging"          # outside public/, so a refused build never reaches the site
    shutil.rmtree(staging, ignore_errors=True)
    words = []
    for rel, (doc, pretty) in docs.items():
        clean = scrub.value(doc)
        words += [f"{rel} {h}" for h in retired_words_in_json(clean)]
        write_json(staging / rel, clean, pretty)
    img = images.write(staging / "img")
    if scrub.counts:
        log(f"scrubbed to aliases: {scrub.counts}")

    problems = gate(staging, cat.get("gate_allow"))
    problems += [f"retired word: {w}" for w in words]
    totals, over = sizes.check(staging)
    problems += over
    if not problems and not args.no_contract:
        ok, out = contract_check(staging)
        if not ok:
            problems.append("the site's contract check failed:\n" + out)
    if problems:
        log("refused; site/public/data is unchanged:\n  " + "\n  ".join(problems))
        return 1
    shutil.rmtree(args.out, ignore_errors=True)
    shutil.move(str(staging), str(args.out))
    n_json = sum(1 for p in args.out.rglob("*.json"))
    log(f"wrote {args.out.relative_to(args.repo) if args.out.is_relative_to(args.repo) else args.out}: "
        f"{len(entries)} campaigns, {n_json} documents, {img['count']} screenshots "
        f"({img['thumbs'] // 1024} KB thumbnails; {img['full_count']} also at full size, {img['fulls'] // 1024} KB); "
        f"{totals['raw'] / 1024:.0f} KB raw, {totals['gz'] / 1024:.0f} KB gzipped of the 8,192 KB budget "
        f"(largest document {totals['largest_doc'][0]}, {totals['largest_doc'][1] / 1024:.1f} KB gz)"
        + ("" if args.no_contract else "; contract checks passed"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
