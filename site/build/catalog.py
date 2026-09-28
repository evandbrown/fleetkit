"""site/catalog.json: the only list of what the site publishes.

A campaign is read from results/<id>/: its campaign.json (the definition as launched) and one directory per
run, <spec>-r<k>. A campaign published before campaigns existed (cap-baseline-1) names its one run directory
under ``before_campaigns`` instead. Nothing else under results/ is read, and results/dev never is.

Each campaign also carries the words the site shows for it (D93): ``title``, a snake_case name of at most four
words; ``answer``, one or two plain sentences with the figures that matter; ``labels``, a short name of at most
four words for each spec, keyed by the spec's name in the definition; ``whys``, why each spec is in the campaign
in at most twelve words, keyed the same way, replacing the definition's why (an empty string removes it); and
``question`` only where the definition's wording has jargon (the definition file is never edited); and, for the
featured campaign, ``featured_spec``, the spec About's card leads with (a spec name).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

FORMAT = "fleetkit-catalog/2"
ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
RESERVED = {"compare"}  # #/results/compare is the compare page (DATA.md, Routes)
RUN_DIR = re.compile(r"^([a-z][a-z0-9]*(?:-[a-z0-9]+)*)-r([1-9]\d*)$")
TITLE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+){0,3}$")  # snake_case, ascii, at most four words: small_c8i_hosts
MAX_LABEL_WORDS = 4
MAX_WHY_WORDS = 12  # every token counts, so "64 GiB" is two: a why is read as a phrase, not a name
# A full stop before a space ends a sentence; "0.50 microVMs" and "c8i.xlarge" hold none.
SENTENCE_END = re.compile(r"[.?!](?=\s)")


class CatalogError(Exception):
    pass


def word_count(text: str) -> int:
    """Words in a short name: the tokens with a letter in them, so "1 vCPU / 1 GiB tuned" is three. contract.ts
    counts the same way."""
    return sum(1 for t in text.split() if re.search(r"[A-Za-z]", t))


def sentences(text: str) -> int:
    return len(SENTENCE_END.findall(text)) + 1


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


def _spec_names(c: dict, w: str) -> set[str]:
    """The names of the campaign's specs, from its definition (a campaign of one names its single spec)."""
    old = c.get("before_campaigns")
    if old:
        return {old["spec"]}
    try:
        definition = json.loads(c["_definition"].read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CatalogError(f"{w}: the definition can't be read: {exc}") from exc
    return set(definition.get("specs") or {})


def _check_words(c: dict, w: str) -> None:
    """The words the site shows for a campaign (D93): the question, the answer, the specs' short names and their
    whys."""
    q = c.get("question")
    if q is not None and (not isinstance(q, str) or not q.strip() or "\n" in q or not q.rstrip().endswith("?")):
        raise CatalogError(f"{w}: the question is one line ending in a question mark")
    a = c.get("answer")
    if a is not None and (not isinstance(a, str) or not a.strip() or "\n" in a or not a.rstrip().endswith(".")
                          or sentences(a.strip()) > 2):
        raise CatalogError(f"{w}: the answer is one or two plain sentences ending in a full stop")
    labels = c.get("labels")
    whys = c.get("whys")
    fs = c.get("featured_spec")
    if labels is None and whys is None and fs is None:
        return
    known = _spec_names(c, w)
    if fs is not None and (not isinstance(fs, str) or fs not in known):
        raise CatalogError(f"{w}: featured_spec names the spec {fs!r}, which the definition doesn't have")
    if labels is not None:
        if not isinstance(labels, dict):
            raise CatalogError(f"{w}: labels map each spec's name to its short name")
        for name, short in labels.items():
            if name not in known:
                raise CatalogError(f"{w}: labels name the spec {name!r}, which the definition doesn't have")
            if not isinstance(short, str) or not short.strip() or "\n" in short or word_count(short) > MAX_LABEL_WORDS:
                raise CatalogError(f"{w}: the short name for {name} is at most four words ({short!r})")
        if len(set(labels.values())) != len(labels):
            raise CatalogError(f"{w}: two specs share a short name")
    if whys is not None:
        # A why replaces the definition's for that spec; an empty string removes it (build_data.py).
        if not isinstance(whys, dict):
            raise CatalogError(f"{w}: whys map each spec's name to why it is in the campaign")
        for name, why in whys.items():
            if name not in known:
                raise CatalogError(f"{w}: whys name the spec {name!r}, which the definition doesn't have")
            if not isinstance(why, str) or "\n" in why or why != why.strip():
                raise CatalogError(f"{w}: the why for {name} is one line with no leading or trailing whitespace ({why!r})")
            if len(why.split()) > MAX_WHY_WORDS:
                raise CatalogError(f"{w}: the why for {name} is at most twelve words ({why!r})")


def load(path: Path, repo: Path) -> dict:
    cat = json.loads(path.read_text(encoding="utf-8"))
    if cat.get("format") != FORMAT:
        raise CatalogError(f"{path}: format should be {FORMAT}")
    seen = set()
    titles = set()
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
        title = c.get("title")
        if not isinstance(title, str) or not TITLE.match(title):
            raise CatalogError(f"{w}: the title is snake_case, ascii, at most four words (small_c8i_hosts), not {title!r}")
        if title in titles:
            raise CatalogError(f"{w}: the title {title!r} is already used")
        titles.add(title)
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
        _check_words(c, w)
    featured = cat.get("featured")
    if featured is not None and featured not in seen:
        raise CatalogError(f"featured: {featured} isn't in the catalog")
    for a in cat.get("gate_allow") or []:
        if not a.get("text") or not a.get("reason"):
            raise CatalogError("gate_allow entries need the exact text and a reason")
    return cat
