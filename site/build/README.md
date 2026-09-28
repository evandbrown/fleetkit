# The dataset builder

Reads the campaigns listed in [../catalog.json](../catalog.json) from `results/` and writes the site's dataset to
`site/public/data/`, exactly as [../DATA.md](../DATA.md) describes. Only catalogued campaigns are read:
`results/<campaign>/campaign.json` (the definition as launched) and its run directories `<spec>-r<k>/`
([docs/spec.md](../../docs/spec.md), "Where results go"). `results/dev/` is refused, and so is a run with no
trials that count (a validation run) or a Docker run.

```
harness/.venv/bin/pip install -r site/build/requirements.txt    # once: Pillow
harness/.venv/bin/python site/build/build_data.py               # writes site/public/data
harness/.venv/bin/python -m pytest site/build/tests             # specs, scrub, rules, real builds
```

`build_data.py --no-contract` skips the site's contract check when Node isn't available; `--out DIR` writes
somewhere else; `--catalog` and `--repo` point it at another catalog and results tree (the tests do).

## What a build does

1. **Reads** each run through the harness's own reader ([source.py](source.py)): `driver.outputs.RunDir`
   translates a directory recorded before the glossary with the harness's one alias map
   (`harness/driver/driver/legacy.py`), and `driver.report.build_report` recomputes every trial's evaluation,
   attribution and cost. The builder keeps no second copy of the old names. A trial that failed outside the
   experiment was set aside by the harness (D63), so it isn't among the run's trials and is never published.
2. **Resolves the specs** from the campaign definition with the schema's own code (`experiments/schema/expand.py`,
   through [specs.py](specs.py)): each named spec is the base merged with its changes, checked against the
   schema. A campaign from before campaigns existed (cap-baseline-1) gets a definition rebuilt from what its run
   recorded. Each run must have recorded the values its spec says. Host vCPUs, memory and price follow from the
   instance type (`experiments/schema/instance-types.json`); none is a spec input.
3. **Builds every document field by field** ([assemble.py](assemble.py)), applying the rules in DATA.md
   ([rules.py](rules.py)), D60 midpoints included. It refuses, rather than repairs, anything that disagrees with
   the harness: a trial's pass value against rule 1, verdicts recomputed from the signals, each density's result,
   the run's result, and cost. For the site's links to GitHub (D73) it records each run's harness commit (null if
   the run recorded none, one with uncommitted changes, or one on none of origin's branches; refused if its records
   disagree) and each campaign's `definition_path`: `experiments/campaigns/<id>.json` when `origin/main` has that
   file and it equals the definition as launched, or a reconstructed campaign's pre-registration. Both are judged
   from this clone's remote-tracking branches, so fetch first; a null link is logged as a note.
4. **Scrubs** every string ([scrub.py](scrub.py)): account ids, ARNs, IP addresses, email addresses and EC2
   instance and image ids become aliases (`account-1`, `ip-2`). Screenshots become WebP with no metadata
   ([images.py](images.py)), one per distinct image: a thumbnail (320 px) of every one, and a full-size copy
   (up to 1,280 px, quality 60) only of those in the trials whose pages open them (DATA.md, rule 10): each run's
   last pass and first failure, and the illustration's filmstrip.
5. **Checks** the staging directory (`site/build/.staging`), and replaces `site/public/data` only if all pass:
   - the gate: no instance or resource ids, ARNs, 12-digit numbers, IP or email addresses, AWS hostnames,
     URLs, availability zones, or image metadata in any published byte (a legitimate match needs a
     `gate_allow` entry in the catalog, with its reason);
   - no retired word in any key or value;
   - the size budget ([sizes.py](sizes.py)): index 5 KB, each document 80 KB, each thumbnail 10 KB, each
     full-size image 60 KB, the dataset 8 MB, all gzipped;
   - the site's own contract ([contract.test.ts](contract.test.ts) runs `src/lib/contract.ts` through vitest).

## Publishing a campaign

Add `{ "id": "<campaign>", "title": "<a short name>" }` to the catalog's `campaigns`, with optional `notes` (the limits
of the result, in plain sentences) and `reading` (Evan's own). The title names it on Results; give the Builder's
Start from the same one in `TITLES` ([draft.ts](../src/components/builder/draft.ts)), which a unit test checks.
Results opens on the newest complete campaign; set the catalog's `featured` to choose another (D58). A campaign of one
from before campaigns existed names its run under `before_campaigns` (see cap-baseline-1).
