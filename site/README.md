# Fleetkit site

The static site we ship at `evan.mx/fleetkit/`: **Campaigns** (the results viewer; it opens on the most recent
campaign), **Builder** (defines a campaign to paste into chat; coming next), **Method** (`docs/method.md`, rendered at
build time) and **About**.

Svelte 5 and Vite, with hash routes and relative URLs (`base: './'`) so it works under any path prefix. No CDN, no web
fonts, no third-party requests.

## Commands

Run these in `site/` with Node 22.12 or later.

| Command | What it does |
|---|---|
| `npm ci` | Installs the pinned dependencies. |
| `npm run dev` | Serves the site with the published dataset (`public/data/`) at http://localhost:5173/. |
| `npm run dev:fixtures` | The same, with the test fixtures (including the synthetic campaign) served at `./data/`. |
| `npm run build` | Builds `dist/`, then checks the size budget. Fails if `public/data/` holds synthetic data. |
| `npm run preview` | Serves `dist/` under `/fleetkit/`, as production does, at http://localhost:4173/fleetkit/. |
| `npm run check` | Type-checks the TypeScript and Svelte. |
| `npm test` | Unit tests: the router, the rules, formatting, retired words, and the fixtures against the contract. |
| `npm run test:e2e` | Browser tests against the built site with the fixtures. Run `npx playwright install chromium` once first. |
| `npx playwright test -c tests/e2e/chrome.config.ts` | The same tests with the Google Chrome installed on this machine; no download. |
| `npx playwright test -c tests/e2e-real/real.config.ts` | Every route over the published dataset (`public/data/`): no failed request, every screenshot decodes, nothing unformatted. `SCREENSHOTS=<dir>` saves each page. |
| `npm run fixtures` | Rebuilds the synthetic campaign and `tests/fixtures/data/index.json`. |

## Data

[DATA.md](DATA.md) is the contract between the dataset builder and the site. The site reads only `public/data/`,
which the builder writes. Until the builder runs, `public/data/index.json` lists no campaigns.

`tests/fixtures/data/` holds two campaigns for tests. `nested-sizes-synthetic` is made up by
[make-synthetic.mjs](tests/fixtures/make-synthetic.mjs) and marked synthetic in every document. `cap-baseline-1` is a
campaign of one, derived from the real run by the one-off
[derive_cap_baseline_1.py](tests/fixtures/derive_cap_baseline_1.py). It has no screenshot files, so its image references
don't resolve.
