# Fixture: the shopping site the task runs against

The fixture is a static shop, "Fleetkit Outfitters", built by `build.py` into
`dist/` and served by nginx (design: [docs/harness-design.md](../docs/harness-design.md),
sections 1, 3 and 4). It exists so the harness measures a browser, not a
website: every byte, request and DOM node is a pure function of the seed, and
`dist/manifest.json` states what a task should cost so the report can flag a
run that moved more or fewer bytes than the site explains.

## Build, check, serve

```sh
python3 fixture/build.py            # writes fixture/dist in about seven seconds, standard library only
python3 fixture/check.py            # serves dist/ on an ephemeral port and verifies the task path
python3 fixture/check.py --all      # ... through all 20 task products in headless Chrome
python3 fixture/check.py --serve    # keep serving after the checks (for a look in a browser)
```

`build.py --seed N` produces a different site; `--out DIR` builds elsewhere;
`--digest` prints a sha256 over the whole tree, which is the same on every
machine for the same seed (the tests build twice and compare).

Tests (offline; Node and Chrome optional):

```sh
python3 -m venv fixture/.venv && fixture/.venv/bin/pip install -r fixture/requirements.txt
fixture/.venv/bin/python -m pytest fixture/tests
```

`check.py` has three layers and skips what it cannot find: plain HTTP checks
of every page on the task path; a Node run of `app.js`'s ranking compared
with `build.py`'s for 40-odd queries; and a headless Chrome walk of the whole
task inside an iframe driver page, which reports the selectors, the badge,
the cart rows, DOM counts, request counts and bytes for each page and
compares them with the manifest. Set `FLEETKIT_CHROME` or `--chrome` to point
at a Chrome or Chromium binary.

## Serving

`dist/` is a plain document root: nginx's stock configuration serves it with
`index.html` at `/`, `/search.html?q=...` (the query string is ignored by
nginx and read by the page), `/p/<id>.html` and `/cart.html`. Mount it at
`/usr/share/nginx/html` in the pinned `nginx:1.29-alpine` image; no custom
nginx config is required or assumed. Stock nginx sends `Last-Modified` and
`ETag` but no `Cache-Control` and does not gzip, which the manifest's byte
model assumes (see below). `dist/` is gitignored; `make fixture` rebuilds it.

**Public copy.** A copy for looking at, not for measuring, is at
https://evan.mx/fleetkit-fixture/. `publish.sh` builds it fresh, re-roots every
URL under `/fleetkit-fixture` (`reroot.py`: the build's references are
root-absolute, and it refuses a copy with a dangling reference), uploads it with
content types and a five-minute cache life, and invalidates the prefix. It runs
by hand with the two values from `infra/site`, the same ones
`site/scripts/deploy.sh` uses; the evan.mx router, a CloudFront Function outside
this repository, must list the prefix. CloudFront compresses and caches the
copy, so its bytes differ from `manifest.json`, which describes the root-served
build the guests get from nginx.

## The task path and its contract

| Step | Page | What the fixture guarantees |
|---|---|---|
| `home` | `/` | `input[name=q]` inside `<form action="/search.html" method="get">` with a submit button; Enter, `form.submit()` and `requestSubmit()` all navigate to `/search.html?q=<query>` |
| `search` | `/search.html?q=` | `app.js` reads `q`, ranks the catalog and renders 24 cards before the load event. Matches are `<a data-testid="result" data-product-id="<id>" href="/p/<id>.html">`; the grid is padded with `data-testid="related"` cards so every query costs the same. `[data-testid=result-count]` states the match count. |
| `open_product` | `/p/<id>.html` | Static page. `h1[data-testid=product-title]` holds exactly the title; a gallery of six images; `button[data-testid=add-to-cart][data-product-id]`. `[data-testid=added]` does not exist until the click. |
| `add_to_cart` | same page | The click writes `localStorage["fleetkit.cart"]`, then after `add_to_cart_delay_ms` (100 ms, in the manifest; a stand-in for a cart API) updates the badge `[data-testid=cart-count]` to `1` and inserts `<p data-testid="added" role="status">Added to cart</p>` after the button, in that order, so a MutationObserver that sees `added` also sees the badge at 1. |
| `verify_cart` | `/cart.html` | `app.js` renders one `<li data-testid="cart-item" data-product-id="<id>">` per cart line with `h3[data-testid=cart-item-title]` holding the title, `[data-testid=cart-item-qty]` and `[data-testid=cart-item-line]`; `[data-testid=cart-empty]` is shown when the cart is empty. |

The cart badge is present on every page (`0` in the static HTML, updated by
`app.js` from localStorage). Product ids look like `p0380`; comparisons are
plain string equality. Cards are single anchors with no nested interactive
elements, so a click anywhere on a result navigates.

`dist/task-products.json` holds exactly 20 entries `{product_id, query,
expected_title}`, spread over the eight departments. Each query is the
shortest of "noun", "adjective noun", "brand noun", "brand adjective noun"
that ranks the product first with a strictly higher score than any other
product, checked at build time with the Python ranking and again in Node and
Chrome by `check.py`. The harness assigns them as `list[slot mod 20]`.

**Search ranking** (identical in `build.py` and `app.js`): tokens are
lower-case runs of `[a-z0-9]`; every query token must match somewhere; a
token scores 5 in the title, 3 in the brand, 2 in the department, 1 in a
tag, summed; ties break by id. Products that match nothing fill the page in
id order rotated by the query's code-point sum, so the DOM and request
counts do not depend on the query.

**State.** The cart lives in `localStorage` for the origin. A second task in
the same browser profile would find the first task's item still there and
fail `verify_cart`'s "exactly one" assertion, so a guest that reuses a
profile must clear storage between tasks (`Storage.clearDataForOrigin` or a
fresh `--user-data-dir`). The design runs one task per microVM, which is a
fresh profile.

## What a page costs

`build.py` refuses to write a build outside the design's envelope (search and
product pages between 1 and 2 MB, 1,500 to 3,000 DOM nodes, 20 to 40 image
requests), so the numbers below cannot drift silently.

| Page | Bytes | Requests | Images | Elements | Nodes |
|---|---|---|---|---|---|
| home `/` | about 1.4 MB | 37 | 33 | 1,423 | 2,032 |
| search | about 1.6 MB | 37 | 32 | 1,598 | 2,294 |
| product | about 1.5 MB | 34 | 30 | 1,545 | 2,194 |
| cart (one item) | about 0.8 MB | 18 | 13 | 778 | 1,116 |

Read the current values from `dist/manifest.json`; the table is indicative.
"Elements" is `document.querySelectorAll('*').length`; "nodes" adds text
nodes (the build emits no whitespace between tags, so every text node is
real text). `check.py` confirms both in Chrome to the exact count.

Images are 8-bit indexed PNGs written by a 20-line pure-Python encoder: a
category silhouette on a gradient, textured with seeded noise so deflate
cannot squeeze them (about 40 KB for 320x240, 86 KB for the 480x360 gallery
shots). Each product has one image of its own; product pages add five shots
from a shared pool of 64, home and search add eight tiles each.

### manifest.json

At the root: `expected_bytes`, `expected_request_count` and `tolerance`
(`{bytes_pct, requests_pct}`) for the whole five-step task, averaged over
the 20 task products, with the exact per-product numbers under
`task.per_product[<product_id>]`. Per page type under `pages.<home|search|
product|cart>`: `bytes_by_class` (`document`, `css`, `js`, `image`; the
catalog ships as `catalog.js` and counts as `js`), `bytes_total`,
`request_count`, `image_request_count`, `dom_element_count`,
`dom_node_count`; the product entry is the mean over all 500 pages with
`_min`/`_max`. Also `selectors` (the table above as CSS selectors),
`add_to_cart_delay_ms`, `seed`, `catalog_size`.

Definitions, chosen to match what Chromium reports over the DevTools protocol:

- `expected_bytes` is the sum over the distinct URLs the four pages
  reference, each counted once. A browser fetches a shared asset (`styles.css`,
  `app.js`, `catalog.js`, a thumbnail that appears on two pages) once and
  serves the later references from cache, for which `Network.loadingFinished.encodedDataLength`
  is 0. Headers add well under 1 percent; a server that gzips the text assets
  would take about 5 percent off; both sit inside `tolerance.bytes_pct`.
  `check.py` measures the same thing through Resource Timing `transferSize`
  and lands within 0.2 percent.
- `expected_request_count` counts every reference on every page, because a
  disk-cache hit still emits `Network.requestWillBeSent`. Later references served
  from the renderer's memory cache emit nothing, so `task.unique_request_count` is
  the floor; the gap is inside `tolerance.requests_pct`. The favicon is
  counted once (Chromium fetches it for the first page).

The report treats a task outside the tolerance as a flag on the fixture or
the network, never as a browser failure (design section 13).

## Layout

- `build.py`: catalog, images, pages, task products, manifest, self-checks.
- `src/app.js`, `src/styles.css`, `src/favicon.svg`: copied into `dist/`;
  `build.py` injects the card template, the cart-row template and the
  add-to-cart delay into `app.js`, so the static pages and the client-side
  rendering share one template.
- `check.py`: local server plus the three verification layers.
- `reroot.py`, `publish.sh`: the public copy (above).
- `tests/`: pytest, builds into a temporary directory.
- `dist/` (generated): `index.html`, `search.html`, `cart.html`, `p/*.html`,
  `catalog.json` and `catalog.js`, `task-products.json`, `manifest.json`,
  `img/p/<id>.png`, `img/g/*.png`, `img/c/*.png`, `img/s/*.png`,
  `img/hero.png`, `app.js`, `styles.css`, `favicon.svg`. About 51 MB, 1,090 files.
