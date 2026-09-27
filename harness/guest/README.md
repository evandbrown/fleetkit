# Guest daemon (`guestd`)

The process that runs inside every session, next to Chromium: a container on the Docker
backend, a Firecracker microVM on AWS. It starts the browser, runs one browser task at a
time over the DevTools protocol, and answers the host daemon on port 8080. The contract
is [docs/harness-design.md](../../docs/harness-design.md), sections 2, 4 and 6; this file
says how the package implements it and what other components can rely on.

Runs on Debian bookworm's `python3` (3.11) with `python3-websockets` 10.4 from apt and
nothing else. `python3 -m guestd` is the whole invocation; production takes no flags.

## Layout

| File | Role |
|---|---|
| `guestd/__main__.py` | CLI: `python3 -m guestd [--selftest \| --serve-site HOST:PORT]`; `FLEETKIT_FAULT` |
| `guestd/server.py` | `/health`, `/task`, `/metrics`, `/logs` on an asyncio HTTP server; one task at a time |
| `guestd/task.py` | the five steps: actions, settle conditions, assertions; failure categories; timing |
| `guestd/cdp.py` | raw CDP on one websocket: one reader task, flat sessions, event queues, listeners |
| `guestd/chromium.py` | launches `/usr/lib/chromium/chromium` with the exact flag list; readiness; relaunch on death |
| `guestd/clock.py` | `Deadline`: the one timeout mechanism for steps, the task and every DevTools wait |
| `guestd/faults.py` | the five faults and what they do |
| `guestd/metrics.py` | `/proc/meminfo` and Chromium's RSS |
| `guestd/log.py` | JSON lines to stdout (the console) and the ring buffer behind `/logs` |
| `guestd/minihttp.py` | the small HTTP/1.1 layer shared by the server, the DevTools probe and the selftest |
| `guestd/selftest.py`, `guestd/selftest_site/` | the bundled site with the design's selectors; `--selftest` |
| `tests/` | pytest, offline except `test_browser.py` (skips without a browser) |

## API on port 8080

Exactly the shapes in design section 4.

**`GET /health`** → `200 {"ready": true, "chromium_version": "Chrome/154.0.8037.57", "uptime_s": 0.5}`
once Chromium answers `/json/version` *and* the browser websocket is connected;
`503 {"ready": false, "chromium_version": null, "uptime_s": ..., "reason": ...}` before,
after a browser crash until the relaunch is up, and forever under `never_ready`.

**`POST /task`** with the `traceparent` header and body
`{task_id, fixture_base_url, product_id, query, expected_title, step_timeout_ms, task_timeout_ms}`
(the two timeouts default to 10000 and 45000). Answers `200` with:

```
{
  "task_id": "...",
  "ok": true, "failure_category": "ok", "failed_step": null, "error": null,
  "steps": [{"name": "home", "dispatch_ns": 85041, "settle_ns": 206020500, "duration_ms": 205.935}, ...],
  "task_ms": 406.809, "bytes_received": 219445, "request_count": 14,
  "screenshot_b64": "<JPEG quality 60, base64>",
  "guest_clock_ns": 1790491954396926750, "traceparent": "<echoed>",
  "log_tail": [{"seq": 5, "ts": ..., "level": "info", "component": "guestd", "msg": "task start", "task_id": "...", ...}, ...]
}
```

On failure the same keys with `ok: false`, `failure_category`, `failed_step`, `error`,
and `steps` holding only the steps that completed (the failed step is named by
`failed_step`, its error by `error`; the driver's `steps.csv` row for it can carry that
error). `task_ms` on a failure is the time from receipt to the failure, so the row still
has a number.

- `dispatch_ns` and `settle_ns` are monotonic nanoseconds **since receipt of the request**;
  `guest_clock_ns` is `time.time_ns()` at receipt. The host adds `guest_clock_ns + offset +
  clock_offset_ns` to place them on its own timeline (design section 8).
- `task_ms` = `settle_ns` of `verify_cart` / 1e6: receipt to the last settle, tab creation
  included; the assertion of `verify_cart`, the screenshot and closing the tab are outside it.
- `duration_ms` = settle - dispatch of that step.
- A second `POST /task` while one runs gets `409 {"ok": false, "error": "...", "task_id": ...}`.
- A task while Chromium is not ready gets `503` with the failure shape and
  `failure_category: "session_not_ready"`.
- A malformed body gets `400 {"ok": false, "error": "..."}`.

**`GET /metrics`** → `{mem_total, mem_available, cached, chromium_rss, tasks_run, tasks_failed}`.
Memory values are **bytes** (from `/proc/meminfo`); `chromium_rss` is the summed RSS of every
process whose `argv[0]` is the Chromium binary (browser, renderers, GPU, utilities); `null`
where there is no `/proc`.

**`GET /logs?since=<seq>`** → `{"since": N, "lines": [...], "next": <seq of the last line returned>, "latest": <newest seq>}`,
at most 1000 lines per call; poll with `since=next`. Each line is
`{seq, ts, level, component, msg, ...fields}` and the same lines go to stdout, so
`docker logs` and the VM console show them too. The ring holds the last 2000.

## The task

Each step is action → settle → assertion, all under one `Deadline`
(`min(step deadline, task deadline)`), and the deadline is the only timeout mechanism: it
bounds every `await` (each CDP command, each event wait, each promise evaluated in the page).
There are no sleeps in the timed region; the only sleeps in the package are the fault
`slow_step` (by definition) and the readiness poll of `/json/version` before the daemon is
ready.

| Step | Action | Settle | Assertion |
|---|---|---|---|
| `home` | `Target.createBrowserContext` + `createTarget` + `attachToTarget {flatten: true}`, `Page.enable`, `Network.enable`; `Page.navigate` to `<base>/` | `Page.loadEventFired` | `input[name=q]` exists |
| `search` | set `input[name=q]` to `query`, `form.requestSubmit()` | `Page.loadEventFired`, then a promise that resolves when `[data-testid=result]` count > 0 (MutationObserver) | a result with `data-product-id == product_id` |
| `open_product` | real mouse click (`Input.dispatchMouseEvent` moved/pressed/released at the element's centre after `scrollIntoView`) on that result, or on the `a[href]`/`button` inside it | `Page.loadEventFired` | `h1[data-testid=product-title]` text == `expected_title` |
| `add_to_cart` | install a MutationObserver promise for `[data-testid=added]` becoming visible, **then** click `button[data-testid=add-to-cart]` | that promise (`Runtime.evaluate awaitPromise`) | cart badge text parses to `1` |
| `verify_cart` | `Page.navigate` to `<base>/cart.html` | `Page.loadEventFired` | exactly one `[data-testid=cart-item]`, `data-product-id == product_id`, title == `expected_title` |

Every task gets a fresh browser context (cookies and `localStorage` isolated) and the
context is disposed afterwards, so a cart from a previous task can never make
`verify_cart` see two items. The tab is created inside the `home` step, so `task_ms`
includes it as the design requires.

"Visible" for `[data-testid=added]` means: exists, has a client rect, and computed
`visibility` is not `hidden`.

**Failure categories** (closed set): `ok`, `step_timeout`, `task_timeout`,
`assertion_failed`, `navigation_error`, `browser_crashed`; the host produces
`guest_unreachable` and `session_not_ready` (the guest returns `session_not_ready` itself
only when asked for a task before Chromium is up).

- `navigation_error`: `Page.navigate` returned `errorText` (`net::ERR_CONNECTION_REFUSED`, ...).
- `browser_crashed`: the DevTools connection dropped, the target crashed, or a command
  failed with "target closed". The supervisor relaunches Chromium; `/health` is 503 until it
  is back and 200 after.
- `assertion_failed`: an assertion did not hold, a JavaScript exception inside one of the
  page snippets (for example the element to click is missing), or any other DevTools
  protocol error (the page did not behave as the task expects).
- `step_timeout` / `task_timeout`: whichever deadline fired; on a tie the task's.

## Faults

`FLEETKIT_FAULT` (containers: `-e`; microVMs: `fleetkit.fault=<name>` on the kernel
command line, exported by init). Unknown names are a configuration error (exit 2).

| Fault | Behaviour |
|---|---|
| `crash_on_start` | exit code 3 before listening |
| `never_ready` | listens, never launches Chromium, `/health` 503 forever |
| `hang_task` | `POST /task` never answers (the connection stays open); `/health` still answers |
| `hang_step` | hangs inside `search` under the step deadline → `step_timeout`, `failed_step: search`, `steps: [home]`, session still ready |
| `slow_step:<ms>` | sleeps `ms` inside `search` under the deadline → `step_timeout` when `ms > step_timeout_ms`, `task_timeout` when the task budget is the smaller one, `ok` otherwise |

## Selectors the fixture must provide

The design names `input[name=q]` (in a form that submits to `/search.html?q=`),
`[data-testid=result]` with `data-product-id`, `h1[data-testid=product-title]`,
`button[data-testid=add-to-cart]`, `[data-testid=added]`, `[data-testid=cart-item]` with
`data-product-id`, and "the cart badge". Two details it leaves open, and what this
daemon does:

- **Cart badge:** `[data-testid=cart-count]` or `[data-testid=cart-badge]`; its text is
  parsed as an integer and must be `1`.
- **Cart item title:** `[data-testid=cart-item-title]` inside the item if present, else the
  item's `data-title` attribute, else the item's trimmed text content.
- The clicked result may be the `<a href>` itself or wrap one.

## Selftest and a fixture stand-in

```
docker run --rm fleetkit-guest:dev --selftest
```

starts the bundled site on loopback (home page padded to 200 kB), the daemon on an
ephemeral loopback port with real Chromium, waits for `/health`, posts one `/task` with
`sku-1001`/`lamp`/`Brass Desk Lamp`, checks five steps, ≥ 200 kB received, a JPEG
screenshot, the echoed `traceparent`, `/metrics` and `/logs`, and exits 0 on pass. The
last line on stderr is `SELFTEST PASS|FAIL: {...}`.

`python3 -m guestd --serve-site 0.0.0.0:8099` serves the same site to anything that can
reach the host, for driving a running guest without the real fixture (from a container:
`fixture_base_url: http://host.docker.internal:8099`).

## Tests

```
python3 -m venv ../.venv && ../.venv/bin/pip install -r requirements.txt
../.venv/bin/python -m pytest -q tests
```

`test_units.py`, `test_server.py` (a stub Chromium over real sockets: every route, 409,
`hang_task`, `never_ready`, `crash_on_start`) and `test_cdp.py` (a fake DevTools websocket)
run anywhere. `test_browser.py` runs the real thing when it finds a browser
(`GUESTD_CHROMIUM`, `/usr/lib/chromium/chromium`, or Google Chrome on macOS) and skips
otherwise: the selftest, every failure category, the deadline categories, fresh contexts
per task, and a browser crash mid-task with relaunch.

## Notes

- Chromium's DevTools HTTP server ignores `Connection: close`; the probe reads by
  `Content-Length`.
- `Page.captureScreenshot` is taken after the timed region, on failures too when the tab
  still exists.
- Chromium's own stderr goes to `/tmp/chromium.stderr.log` (tmpfs in a VM); the last 20
  lines are logged when the process dies.
