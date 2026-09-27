#!/usr/bin/env python3
"""Serve fixture/dist locally and verify the harness task path against it.

Three layers, each skipped gracefully when its tool is missing:

1. HTTP: every page on the task path answers 200 with the design's selectors,
   the product title matches, catalog.json has 500 products, and the Python
   ranking puts each task product first for its query.
2. Node: app.js's rankProducts is loaded in a bare Node context and its
   first-page card order is compared with build.py's for many queries.
3. Chrome: headless Chrome walks home -> search -> product -> add to cart ->
   cart inside an iframe driver page and reports what the harness will see
   (selectors, badge, cart rows, DOM counts, resource counts and bytes),
   which is then compared with dist/manifest.json.

    python3 fixture/check.py [--dist fixture/dist] [--all] [--chrome PATH] [--no-chrome]

Exit status is non-zero on any failed check. Standard library only.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402

CHROME_CANDIDATES = [
    os.environ.get("FLEETKIT_CHROME", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
]

# The driver page: same origin as the fixture, so it can script the iframe it
# walks through the task. It writes a JSON log into <pre id="out">, which
# `chrome --dump-dom` prints once the virtual time budget runs out.
FLOW_PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>fixture check</title></head>
<body><pre id="out"></pre><iframe id="f" width="1280" height="800" src="/"></iframe>
<script>
(function () {
  var params = new URLSearchParams(location.search);
  var PID = params.get('pid'), QUERY = params.get('q'), TITLE = params.get('title');
  var out = document.getElementById('out'), f = document.getElementById('f');
  var log = {steps: []};
  function emit() { out.textContent = JSON.stringify(log); }
  function fail(msg) { log.error = msg; emit(); }
  function counts(d) {
    var elements = d.querySelectorAll('*').length, nodes = 0;
    var w = d.createTreeWalker(d, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    while (w.nextNode()) nodes++;
    return {elements: elements, nodes: nodes};
  }
  function resources(win) {
    var nav = win.performance.getEntriesByType('navigation')[0];
    var res = win.performance.getEntriesByType('resource');
    var bytes = nav ? nav.encodedBodySize : 0, transfer = nav ? nav.transferSize : 0, images = 0;
    res.forEach(function (r) { bytes += r.encodedBodySize; transfer += r.transferSize; if (r.initiatorType === 'img') images++; });
    // bytes: body bytes whether or not the cache served them; transfer: bytes on
    // the wire with headers, 0 for a cache hit (what CDP's encodedDataLength sees).
    return {requests: res.length + 1, bytes: bytes, transfer: transfer, images: images};
  }
  function record(name, d, win, extra) {
    var s = {name: name, url: win.location.pathname + win.location.search};
    var c = counts(d); s.elements = c.elements; s.nodes = c.nodes;
    var r = resources(win); s.requests = r.requests; s.bytes = r.bytes; s.transfer = r.transfer; s.images = r.images;
    for (var k in extra) s[k] = extra[k];
    log.steps.push(s); emit();
  }
  var phase = 'home';
  f.addEventListener('load', function () {
    var d = f.contentDocument, win = f.contentWindow;
    try {
      if (phase === 'home') {
        var input = d.querySelector('input[name=q]');
        record('home', d, win, {has_input: !!input});
        if (!input) return fail('no input[name=q] on home');
        input.value = QUERY;
        phase = 'search';
        input.form.requestSubmit();
      } else if (phase === 'search') {
        var settle = function () {
          var results = d.querySelectorAll('[data-testid=result]');
          if (!results.length) return false;
          var index = -1;
          results.forEach(function (r, i) { if (r.getAttribute('data-product-id') === PID) index = i; });
          record('search', d, win, {result_count: results.length, target_index: index,
                                    count_text: (d.querySelector('[data-testid=result-count]') || {}).textContent});
          if (index < 0) { fail('target not among results'); return true; }
          phase = 'product';
          results[index].click();
          return true;
        };
        if (!settle()) {
          var mo = new MutationObserver(function () { if (settle()) mo.disconnect(); });
          mo.observe(d.body, {childList: true, subtree: true});
        }
      } else if (phase === 'product') {
        var h1 = d.querySelector('h1[data-testid=product-title]');
        var btn = d.querySelector('button[data-testid=add-to-cart]');
        var addedBefore = d.querySelector('[data-testid=added]');
        record('product', d, win, {title: h1 ? h1.textContent : null, title_ok: !!h1 && h1.textContent === TITLE,
                                   has_button: !!btn, added_before_click: !!addedBefore,
                                   badge_before: (d.querySelector('[data-testid=cart-count]') || {}).textContent});
        if (!btn) return fail('no add-to-cart button');
        var seen = false;
        var mo2 = new MutationObserver(function () {
          var a = d.querySelector('[data-testid=added]');
          if (!a || seen) return;
          var visible = !!(a.offsetWidth || a.offsetHeight || a.getClientRects().length);
          if (!visible) return;
          seen = true; mo2.disconnect();
          log.steps.push({name: 'add_to_cart', added_text: a.textContent,
                          badge: (d.querySelector('[data-testid=cart-count]') || {}).textContent,
                          storage: win.localStorage.getItem('fleetkit.cart')});
          emit();
          phase = 'cart';
          win.location.href = '/cart.html';
        });
        mo2.observe(d.body, {childList: true, subtree: true, attributes: true});
        btn.click();
      } else if (phase === 'cart') {
        var items = d.querySelectorAll('[data-testid=cart-item]');
        var first = items[0];
        record('cart', d, win, {
          item_count: items.length,
          item_pid: first ? first.getAttribute('data-product-id') : null,
          item_title: first ? (first.querySelector('[data-testid=cart-item-title]') || {}).textContent : null,
          badge: (d.querySelector('[data-testid=cart-count]') || {}).textContent
        });
        log.done = true; emit();
      }
    } catch (e) { fail(String(e && e.stack || e)); }
  });
})();
</script></body></html>
"""


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet
        pass

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass  # Chrome was killed mid-transfer; not a fixture problem

    def do_GET(self):
        if urlparse(self.path).path == "/__check/flow":
            body = FLOW_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


def serve(dist: Path, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(dist)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:{}".format(server.server_address[1])


def get(url: str) -> tuple[int, bytes]:
    with urlopen(url, timeout=10) as r:
        return r.status, r.read()


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.notes: list[str] = []

    def check(self, ok: bool, msg: str) -> bool:
        print(("  ok   " if ok else "  FAIL ") + msg)
        if not ok:
            self.failures.append(msg)
        return ok

    def note(self, msg: str) -> None:
        print("  note " + msg)
        self.notes.append(msg)


def load_products(dist: Path) -> list[dict]:
    products = json.loads((dist / "catalog.json").read_text())["products"]
    for p in products:
        p["_idx"] = build.index_product(p)
    return products


def check_http(base: str, dist: Path, tasks: list[dict], rep: Report) -> None:
    print("HTTP checks against " + base)
    products = load_products(dist)
    rep.check(len(products) == build.CATALOG_SIZE, "catalog.json has {} products".format(len(products)))
    rep.check(len(tasks) == build.TASK_PRODUCTS, "task-products.json has {} entries".format(len(tasks)))
    rep.check(all(set(t) == {"product_id", "query", "expected_title"} for t in tasks), "task entries carry exactly product_id, query, expected_title")
    status, body = get(base + "/")
    rep.check(status == 200 and b'name="q"' in body, "GET / is 200 and has input[name=q]")
    rep.check(b'action="/search.html"' in body and b'method="get"' in body, "home search form submits GET /search.html")
    status, body = get(base + "/cart.html")
    rep.check(status == 200 and b'id="cart-items"' in body, "GET /cart.html is 200 with the cart list")
    status, body = get(base + "/manifest.json")
    rep.check(status == 200, "GET /manifest.json is 200")
    by_id = {p["id"]: p for p in products}
    for t in tasks:
        p = by_id[t["product_id"]]
        cards = build.search_page_cards(products, t["query"])
        first = cards[0][0]["id"] if cards else None
        rep.check(first == p["id"], "python ranking puts {} first for {!r}".format(p["id"], t["query"]))
        status, body = get("{}/search.html?q={}".format(base, quote_plus(t["query"])))
        rep.check(status == 200 and b'id="results"' in body, "GET /search.html?q=... is 200 for {}".format(p["id"]))
        status, body = get("{}/p/{}.html".format(base, p["id"]))
        text = body.decode("utf-8")
        m = re.search(r'<h1 class="product-title" data-testid="product-title">(.*?)</h1>', text)
        title = html.unescape(m.group(1)) if m else None
        rep.check(status == 200 and title == t["expected_title"], "GET /p/{}.html title == expected_title".format(p["id"]))
        rep.check('data-testid="add-to-cart"' in text and 'data-testid="added"' not in text,
                  "product page has the add-to-cart button and no [data-testid=added] before the click")
        rep.check(status == 200 and get("{}{}".format(base, p["image"]))[0] == 200, "product image {} is served".format(p["image"]))


NODE_RANK = r"""
const fs = require('fs');
const [,, dist, queriesJson] = process.argv;
const catalog = JSON.parse(fs.readFileSync(dist + '/catalog.json', 'utf8'));
const window = { FLEETKIT_CATALOG: catalog, location: { search: '' }, localStorage: { getItem: () => null, setItem: () => {} }, setTimeout };
const document = { body: { getAttribute: () => 'none' }, querySelector: () => null, querySelectorAll: () => [], getElementById: () => null };
new Function('window', 'document', fs.readFileSync(dist + '/app.js', 'utf8'))(window, document);
const out = {};
for (const q of JSON.parse(queriesJson)) {
  const r = window.Fleetkit.rankProducts(catalog.products, q);
  const ids = r.matches.slice(0, 24).map(m => [m.product.id, true]);
  for (let i = 0; ids.length < 24 && i < r.filler.length; i++) ids.push([r.filler[i].id, false]);
  out[q] = ids;
}
process.stdout.write(JSON.stringify(out));
"""


def node_rank(dist: Path, queries: list[str]) -> dict | None:
    node = shutil.which("node")
    if not node:
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(NODE_RANK)
        script = f.name
    try:
        res = subprocess.run([node, script, str(dist), json.dumps(queries)], capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(script)
    if res.returncode != 0:
        raise RuntimeError("node ranking failed: " + res.stderr)
    return json.loads(res.stdout)


def check_node(dist: Path, tasks: list[dict], rep: Report) -> None:
    print("Node cross-check of app.js ranking")
    products = load_products(dist)
    queries = [t["query"] for t in tasks]
    queries += [slug for slug, _, _ in build.CATEGORIES] + [b.lower() for b in build.BRANDS[:6]]
    queries += ["", "bestseller", "trail running", "zzzz nothing", "kettle & board", "Ondo", "LAMP", "eu 42"]
    result = node_rank(dist, queries)
    if result is None:
        rep.note("node not found; skipping the JS ranking cross-check")
        return
    mismatches = 0
    for q in queries:
        py = [[p["id"], m] for p, m in build.search_page_cards(products, q)]
        if py != result[q]:
            mismatches += 1
            print("    mismatch for {!r}: python {} js {}".format(q, py[:3], result[q][:3]))
    rep.check(mismatches == 0, "JS and Python agree on the first page for {} queries".format(len(queries)))


def find_chrome(explicit: str | None) -> str | None:
    for cand in ([explicit] if explicit else []) + CHROME_CANDIDATES:
        if not cand:
            continue
        if os.path.isabs(cand) and os.access(cand, os.X_OK):
            return cand
        found = shutil.which(cand)
        if found:
            return found
    return None


def run_chrome_dump(chrome: str, url: str, timeout_s: int) -> str:
    """`chrome --dump-dom` prints the DOM and, on some builds, then lingers with
    helper processes holding stdout open, so read until </html> and kill the group."""
    with tempfile.TemporaryDirectory(prefix="fleetkit-check-") as profile:
        cmd = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
               "--disable-background-networking", "--disable-component-update", "--disable-sync",
               "--user-data-dir=" + profile, "--window-size=1280,800",
               "--virtual-time-budget=30000", "--dump-dom", url]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        chunks: list[bytes] = []
        deadline = time.monotonic() + timeout_s
        try:
            assert proc.stdout is not None
            os.set_blocking(proc.stdout.fileno(), False)
            while time.monotonic() < deadline:
                ready, _, _ = select.select([proc.stdout], [], [], 0.5)
                if ready:
                    data = proc.stdout.read()
                    if data:
                        chunks.append(data)
                    elif proc.poll() is not None:
                        break
                if chunks and b"</html>" in chunks[-1]:
                    break
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        return b"".join(chunks).decode("utf-8", "replace")


def run_flow(chrome: str, base: str, task: dict, timeout_s: int = 120) -> dict:
    url = "{}/__check/flow?pid={}&q={}&title={}".format(base, task["product_id"], quote_plus(task["query"]),
                                                      quote_plus(task["expected_title"]))
    dom = run_chrome_dump(chrome, url, timeout_s)
    m = re.search(r'<pre id="out">(.*?)</pre>', dom, re.S)
    if not m or not m.group(1).strip():
        raise RuntimeError("no flow output from chrome; got: " + dom[-500:])
    return json.loads(html.unescape(m.group(1)))


def within(value: float, expected: float, pct: float) -> bool:
    return abs(value - expected) <= expected * pct / 100


def check_chrome(chrome: str, base: str, manifest: dict, tasks: list[dict], rep: Report) -> None:
    print("Chrome task-path checks with " + chrome)
    pages = manifest["pages"]
    for t in tasks:
        flow = run_flow(chrome, base, t)
        steps = {s["name"]: s for s in flow.get("steps", [])}
        pid = t["product_id"]
        if not rep.check(flow.get("done") is True and "error" not in flow, "{}: flow completed ({})".format(pid, flow.get("error", "ok"))):
            print("    " + json.dumps(flow)[:1500])
            continue
        s = steps["search"]
        rep.check(s["target_index"] == 0, "{}: search {!r} lists the product first among {} results".format(pid, t["query"], s["result_count"]))
        pr = steps["product"]
        rep.check(pr["title_ok"], "{}: h1[data-testid=product-title] == expected_title".format(pid))
        rep.check(pr["added_before_click"] is False and pr["badge_before"] == "0", "{}: no [data-testid=added] and badge 0 before the click".format(pid))
        a = steps["add_to_cart"]
        rep.check(a["badge"] == "1", "{}: [data-testid=added] appeared and cart badge == 1".format(pid))
        c = steps["cart"]
        rep.check(c["item_count"] == 1 and c["item_pid"] == pid and c["item_title"] == t["expected_title"],
                  "{}: cart has exactly one [data-testid=cart-item] with the id and title".format(pid))
        for name in ("home", "search", "product", "cart"):
            m = pages[name]
            st = steps[name]
            exp_el, exp_nodes = m["dom_element_count"], m["dom_node_count"]
            rep.check(within(st["elements"], exp_el, 2) and within(st["nodes"], exp_nodes, 2),
                      "{}: {} DOM {} elements / {} nodes (manifest {} / {})".format(pid, name, st["elements"], st["nodes"], exp_el, exp_nodes))
            # Resource Timing does not list the favicon; the manifest counts it. The
            # cart shows one image fewer when the item is also a recommendation.
            rep.check(abs(st["images"] - m["image_request_count"]) <= 1 and abs(st["requests"] - m["request_count"]) <= 2,
                      "{}: {} loaded {} requests, {} images (manifest {} / {})".format(pid, name, st["requests"], st["images"], m["request_count"], m["image_request_count"]))
            if name == "home":
                # Only the first page is cold. Later pages reuse assets from the
                # memory cache, which Resource Timing reports with a zero body size.
                rep.check(within(st["bytes"], m["bytes_total"], 5),
                          "{}: {} received {} bytes (manifest {})".format(pid, name, st["bytes"], m["bytes_total"]))
        per = manifest["task"]["per_product"][pid]
        tol = manifest["tolerance"]
        got_transfer = sum(steps[n]["transfer"] for n in ("home", "search", "product", "cart"))
        got_requests = sum(steps[n]["requests"] for n in ("home", "search", "product", "cart"))
        rep.check(within(got_transfer, per["expected_bytes"], tol["bytes_pct"]),
                  "{}: whole task moved {} bytes on the wire; manifest expects {} +/-{}%".format(pid, got_transfer, per["expected_bytes"], tol["bytes_pct"]))
        rep.check(within(got_requests, per["expected_request_count"], tol["requests_pct"]),
                  "{}: whole task made {} requests; manifest expects {} +/-{}% (unique floor {})".format(
                      pid, got_requests, per["expected_request_count"], tol["requests_pct"], per["unique_request_count"]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dist", type=Path, default=HERE / "dist")
    ap.add_argument("--port", type=int, default=0, help="serve on this port (default: ephemeral)")
    ap.add_argument("--all", action="store_true", help="drive Chrome through all 20 task products (default: 3)")
    ap.add_argument("--chrome", help="path to a Chrome/Chromium binary")
    ap.add_argument("--no-chrome", action="store_true")
    ap.add_argument("--no-node", action="store_true")
    ap.add_argument("--serve", action="store_true", help="after the checks, keep serving until interrupted")
    args = ap.parse_args(argv)

    dist = args.dist.resolve()
    if not (dist / "manifest.json").exists():
        print("no build at {}; run python3 fixture/build.py first".format(dist), file=sys.stderr)
        return 2
    manifest = json.loads((dist / "manifest.json").read_text())
    tasks = json.loads((dist / "task-products.json").read_text())
    rep = Report()
    server, base = serve(dist, args.port)
    print("serving {} at {}".format(dist, base))
    try:
        check_http(base, dist, tasks, rep)
        if not args.no_node:
            check_node(dist, tasks, rep)
        if not args.no_chrome:
            chrome = find_chrome(args.chrome)
            if chrome:
                check_chrome(chrome, base, manifest, tasks if args.all else tasks[:3], rep)
            else:
                rep.note("no Chrome/Chromium found (set FLEETKIT_CHROME); skipping the browser walk")
        if args.serve:
            print("serving until interrupted; the task path is at {}/".format(base))
            threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    print("{} failures, {} notes".format(len(rep.failures), len(rep.notes)))
    return 1 if rep.failures else 0


if __name__ == "__main__":
    sys.exit(main())
