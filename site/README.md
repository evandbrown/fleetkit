# Fleetkit site

The static site we ship at `evan.mx/fleetkit/`, a design document for technical reviewers: **Results** (the campaigns,
opening on the featured one, with Compare specs as an action), **Builder** (defines a campaign to paste into chat) and
**About** (what we evaluate, the architecture, the task, the SLOs, how we test and what we measure). Light theme only.
Routes are listed in [DATA.md](DATA.md#routes) and [src/lib/router.ts](src/lib/router.ts).

Svelte 5 and Vite, with hash routes and relative URLs (`base: './'`) so it works under any path prefix. No CDN, no web
fonts, no third-party requests.

## Commands

Run these in `site/` with Node 22.12 or later.

| Command | What it does |
|---|---|
| `npm ci` | Installs the pinned dependencies. |
| `npm run dev` | Serves the site with the published dataset (`public/data/`) at http://localhost:5173/. |
| `npm run dev:fixtures` | The same, with the test fixtures (including the synthetic campaign) served at `./data/`. |
| `npm run build` | Builds `dist/`, then checks the size budget (70 KB gz loaded first, 40 KB per lazy chunk). Fails if `public/data/` holds synthetic data. |
| `npm run preview` | Serves `dist/` under `/fleetkit/`, as production does, at http://localhost:4173/fleetkit/. |
| `npm run check` | Type-checks the TypeScript and Svelte. |
| `npm test` | Unit tests: the router, the rules, formatting, retired words, and the fixtures against the contract. |
| `npm run test:e2e` | Browser tests against the built site with the fixtures. Run `npx playwright install chromium` once first. |
| `npx playwright test -c tests/e2e/chrome.config.ts` | The same tests with the Google Chrome installed on this machine; no download. |
| `npx playwright test -c tests/e2e-real/real.config.ts` | Every route over the published dataset (`public/data/`): no failed request, every screenshot decodes, nothing unformatted. `SCREENSHOTS=<dir>` saves each page. |
| `npm run fixtures` | Rebuilds the synthetic campaigns, copies cap-baseline-1 from `public/data/` (without screenshots), and rebuilds `tests/fixtures/data/index.json`. |

## Data

[DATA.md](DATA.md) is the contract between the dataset builder and the site. The site reads only `public/data/`,
which the builder writes. Until the builder runs, `public/data/index.json` lists no campaigns.

`tests/fixtures/data/` holds three campaigns for tests. `nested-sizes-synthetic` and `nested-hv-synthetic` are made up
by [make-synthetic.mjs](tests/fixtures/make-synthetic.mjs) and marked synthetic in every document: replicas that
disagree, a run that stopped early, densities not tested, and specs with no midpoint yet. `cap-baseline-1` is the
builder's real output copied from `public/data/`, without its screenshot files, so its image references don't resolve.
