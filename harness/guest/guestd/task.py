"""The one standard task: home, search, open_product, add_to_cart, verify_cart.

Design section 4 defines each step's action, the point it settles at, its assertion, the
failure categories (a closed set) and how time is measured:

- ``task_ms`` runs on the monotonic clock from receipt of ``POST /task`` to the settle of
  ``verify_cart``, tab creation included.
- every step's ``dispatch_ns`` and ``settle_ns`` are monotonic nanoseconds since request
  receipt (offsets the host adds to ``guest_clock_ns`` after correcting the clock offset);
  ``duration_ms`` is settle minus dispatch.
- ``step_timeout_ms`` wraps every step and ``task_timeout_ms`` wraps the task, through the
  same :class:`~guestd.clock.Deadline` mechanism as every DevTools wait. There are no
  fixed sleeps in the timed region; DOM states are awaited with MutationObserver
  promises.
- ``bytes_received`` sums ``Network.loadingFinished.encodedDataLength`` for the task's tab
  and ``request_count`` counts ``Network.requestWillBeSent``. Each step carries its share:
  the counts between its dispatch and the next step's dispatch (the last step's window ends
  at the end of the timed region), so tab creation belongs to ``home`` and the steps of an
  ok task sum exactly to the task's totals. On a failure the failed step is not in
  ``steps``; its window's traffic is the totals minus the sum of the listed steps.
- the end of the timed region, for the counters, the final proc sample and
  ``guestd_cpu_ms``, is the point after the last step (its assertion included) or the
  failure, before the screenshot.
- ``proc_samples`` (see :mod:`guestd.procstat`) samples the guest every
  ``sample_interval_ms`` from receipt to the end of the timed region; ``guestd_cpu_ms`` is
  the daemon's own CPU time (user + system, all threads) over the same span.
- with ``screenshot_each_step`` a screenshot is taken right after each step settles and
  its assertion passes, inside the task's wall time (bounded by the task deadline) but
  outside every step's duration; ``task_ms`` then includes them, so ``timing_valid`` is
  false. Without it ``timing_valid`` is true.
- the screenshot (``Page.captureScreenshot`` JPEG quality 60) is taken after the timed
  region, for failures too when the tab still exists.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .cdp import BrowserGone, CDPClient, CDPError, EventQueue
from .clock import Deadline, DeadlineExpired, never
from .faults import FAULT_STEP, Fault
from .procstat import ProcSampler, TaskSampling

STEP_NAMES = ("home", "search", "open_product", "add_to_cart", "verify_cart")

FAILURE_CATEGORIES = (
    "ok",
    "step_timeout",
    "task_timeout",
    "assertion_failed",
    "navigation_error",
    "browser_crashed",
    "guest_unreachable",
    "microvm_not_ready",
)

DEFAULT_STEP_TIMEOUT_MS = 10000
DEFAULT_TASK_TIMEOUT_MS = 45000
DEFAULT_SAMPLE_INTERVAL_MS = 200
# A shorter non-zero interval would spend more of the event loop on /proc than on the task.
MIN_SAMPLE_INTERVAL_MS = 10

# Budgets outside the timed region: the screenshot and closing the tab.
SCREENSHOT_TIMEOUT_S = 10.0
CLOSE_TIMEOUT_S = 5.0

# Selectors that the design names explicitly.
SEL_SEARCH_INPUT = "input[name=q]"
SEL_RESULT = "[data-testid=result]"
SEL_PRODUCT_TITLE = "h1[data-testid=product-title]"
SEL_ADD_TO_CART = "button[data-testid=add-to-cart]"
SEL_ADDED = "[data-testid=added]"
SEL_CART_ITEM = "[data-testid=cart-item]"
# The design says "cart badge count" without naming a selector; either spelling is accepted.
SEL_CART_BADGE = "[data-testid=cart-count], [data-testid=cart-badge]"
# The cart item's title: a nested element if the fixture marks one, else data-title, else text.
SEL_CART_ITEM_TITLE = "[data-testid=cart-item-title]"


class TaskError(Exception):
    """A categorized task failure."""

    def __init__(self, category: str, message: str) -> None:
        assert category in FAILURE_CATEGORIES
        self.category = category
        self.message = message
        super().__init__(f"{category}: {message}")


@dataclass
class TaskRequest:
    task_id: str
    fixture_base_url: str
    product_id: str
    query: str
    expected_title: str
    step_timeout_ms: int = DEFAULT_STEP_TIMEOUT_MS
    task_timeout_ms: int = DEFAULT_TASK_TIMEOUT_MS
    sample_interval_ms: int = DEFAULT_SAMPLE_INTERVAL_MS
    screenshot_each_step: bool = False

    @classmethod
    def from_dict(cls, d: Any) -> "TaskRequest":
        if not isinstance(d, dict):
            raise ValueError("task body must be a JSON object")
        missing = [k for k in ("task_id", "fixture_base_url", "product_id", "query", "expected_title") if not d.get(k)]
        if missing:
            raise ValueError("missing or empty field(s): " + ", ".join(missing))
        for k in ("task_id", "fixture_base_url", "product_id", "query", "expected_title"):
            if not isinstance(d[k], str):
                raise ValueError(f"{k} must be a string")
        base = d["fixture_base_url"].rstrip("/")
        if not (base.startswith("http://") or base.startswith("https://")):
            raise ValueError("fixture_base_url must be an http(s) URL")

        def _ms(name: str, default: int) -> int:
            v = d.get(name, default)
            if v is None:
                return default
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                raise ValueError(f"{name} must be a positive number of milliseconds")
            return int(v)

        interval = d.get("sample_interval_ms", DEFAULT_SAMPLE_INTERVAL_MS)
        if interval is None:
            interval = DEFAULT_SAMPLE_INTERVAL_MS
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval != int(interval):
            raise ValueError("sample_interval_ms must be an integer number of milliseconds")
        interval = int(interval)
        if interval != 0 and interval < MIN_SAMPLE_INTERVAL_MS:
            raise ValueError(f"sample_interval_ms must be 0 (no sampling) or at least {MIN_SAMPLE_INTERVAL_MS}")
        each = d.get("screenshot_each_step", False)
        if each is None:
            each = False
        if not isinstance(each, bool):
            raise ValueError("screenshot_each_step must be a boolean")

        return cls(
            task_id=d["task_id"],
            fixture_base_url=base,
            product_id=d["product_id"],
            query=d["query"],
            expected_title=d["expected_title"],
            step_timeout_ms=_ms("step_timeout_ms", DEFAULT_STEP_TIMEOUT_MS),
            task_timeout_ms=_ms("task_timeout_ms", DEFAULT_TASK_TIMEOUT_MS),
            sample_interval_ms=interval,
            screenshot_each_step=each,
        )


@dataclass
class StepRecord:
    name: str
    dispatch_ns: int
    settle_ns: int
    duration_ms: float
    bytes_received: Optional[int] = None
    request_count: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "dispatch_ns": self.dispatch_ns,
            "settle_ns": self.settle_ns,
            "duration_ms": self.duration_ms,
            "bytes_received": self.bytes_received,
            "request_count": self.request_count,
        }


@dataclass
class Tab:
    session_id: str  # the CDP session of the attached target (CDP's own term)
    target_id: str
    context_id: str
    events: EventQueue
    bytes_received: int = 0
    request_count: int = 0
    listener: Any = None


@dataclass
class TaskResult:
    task_id: str
    ok: bool
    failure_category: str
    steps: List[StepRecord]
    task_ms: Optional[float]
    bytes_received: int
    request_count: int
    screenshot_b64: Optional[str]
    failed_step: Optional[str] = None
    error: Optional[str] = None
    proc_samples: List[Dict[str, Any]] = field(default_factory=list)
    sample_interval_ms: Optional[int] = None
    guestd_cpu_ms: Optional[float] = None
    timing_valid: bool = True
    step_screenshots: Optional[List[Dict[str, Any]]] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "task_id": self.task_id,
            "ok": self.ok,
            "failure_category": self.failure_category,
            "failed_step": self.failed_step,
            "error": self.error,
            "steps": [s.as_dict() for s in self.steps],
            "task_ms": self.task_ms,
            "bytes_received": self.bytes_received,
            "request_count": self.request_count,
            "screenshot_b64": self.screenshot_b64,
            "proc_samples": self.proc_samples,
            "sample_interval_ms": self.sample_interval_ms,
            "guestd_cpu_ms": self.guestd_cpu_ms,
            "timing_valid": self.timing_valid,
        }
        if self.step_screenshots is not None:
            d["step_screenshots"] = self.step_screenshots
        d.update(self.extra)
        return d


# ---------------------------------------------------------------------------------
# JavaScript evaluated in the page. Every snippet is a complete expression; string
# arguments are injected as JSON literals.
# ---------------------------------------------------------------------------------

JS_EXISTS = "!!document.querySelector({sel})"

JS_SUBMIT_SEARCH = """(() => {{
  const input = document.querySelector({sel});
  if (!input) throw new Error({sel} + ' not found');
  input.focus();
  input.value = {query};
  input.dispatchEvent(new Event('input', {{bubbles: true}}));
  const form = input.form;
  if (!form) throw new Error({sel} + ' is not inside a form');
  if (typeof form.requestSubmit === 'function') form.requestSubmit(); else form.submit();
  return true;
}})()"""

JS_RESULTS_PRESENT = """new Promise((resolve) => {{
  const count = () => document.querySelectorAll({sel}).length;
  if (count() > 0) return resolve(count());
  const mo = new MutationObserver(() => {{
    const c = count();
    if (c > 0) {{ mo.disconnect(); resolve(c); }}
  }});
  mo.observe(document.documentElement, {{childList: true, subtree: true, attributes: true}});
}})"""

JS_RESULT_FOR_PRODUCT = """(() => {{
  const items = Array.from(document.querySelectorAll({sel}));
  const hit = items.find((el) => el.getAttribute('data-product-id') === {pid});
  return {{count: items.length, found: !!hit}};
}})()"""

# Locates the element to click, scrolls it into view and returns the centre of its box
# in CSS pixels, which is what Input.dispatchMouseEvent wants. If the element itself is
# not a link or button but wraps one, the inner control is the click target.
JS_CLICK_POINT = """(() => {{
  const el = ({el_expr});
  if (!el) throw new Error({desc} + ' not found');
  const target = el.matches('a[href],button,[role=button]') ? el : (el.querySelector('a[href],button') || el);
  target.scrollIntoView({{block: 'center', inline: 'center'}});
  const r = target.getBoundingClientRect();
  if (!(r.width > 0 && r.height > 0)) throw new Error({desc} + ' has no box to click');
  return {{x: r.left + r.width / 2, y: r.top + r.height / 2}};
}})()"""

JS_EL_RESULT_FOR_PRODUCT = "Array.from(document.querySelectorAll({sel})).find((el) => el.getAttribute('data-product-id') === {pid})"

JS_TEXT = """(() => {{
  const el = document.querySelector({sel});
  return el ? (el.textContent || '').trim() : null;
}})()"""

# Installed before the click so the observer cannot miss a change that happens at once.
JS_INSTALL_ADDED_WATCH = """(() => {{
  const visible = () => {{
    const el = document.querySelector({sel});
    if (!el) return false;
    if (!el.getClientRects().length) return false;
    return getComputedStyle(el).visibility !== 'hidden';
  }};
  window.__fleetkit_added = new Promise((resolve) => {{
    if (visible()) return resolve(true);
    const mo = new MutationObserver(() => {{
      if (visible()) {{ mo.disconnect(); resolve(true); }}
    }});
    mo.observe(document.documentElement, {{childList: true, subtree: true, attributes: true, characterData: true}});
  }});
  return true;
}})()"""

JS_AWAIT_ADDED = "window.__fleetkit_added"

JS_CART_BADGE = """(() => {{
  const el = document.querySelector({sel});
  if (!el) return null;
  const n = parseInt((el.textContent || '').trim(), 10);
  return Number.isNaN(n) ? (el.textContent || '').trim() : n;
}})()"""

JS_CART_ITEMS = """(() => Array.from(document.querySelectorAll({sel})).map((el) => {{
  const t = el.querySelector({title_sel});
  const title = t ? t.textContent : (el.getAttribute('data-title') || el.textContent);
  return {{product_id: el.getAttribute('data-product-id'), title: (title || '').trim()}};
}}))()"""


def _js(template: str, _raw: Optional[Dict[str, str]] = None, **kw: Any) -> str:
    """Fill a snippet: keyword strings become JSON literals; ``_raw`` entries are spliced as code."""
    values = {k: json.dumps(v) if isinstance(v, str) else v for k, v in kw.items()}
    if _raw:
        values.update(_raw)
    return template.format(**values)


class TaskRunner:
    """Runs one task on one browser connection. Not reentrant: the server serializes tasks."""

    def __init__(self, client: CDPClient, log, fault: Optional[Fault] = None, sampler: Optional[ProcSampler] = None) -> None:
        self._client = client
        self._log = log
        self._fault = fault
        self._sampler = sampler

    async def run(self, req: TaskRequest, t0_ns: int, cpu0_ns: Optional[int] = None) -> TaskResult:
        """``t0_ns`` is ``time.monotonic_ns()`` at request receipt: the task clock's zero;
        ``cpu0_ns`` is ``time.process_time_ns()`` at the same moment (now if not given)."""
        if cpu0_ns is None:
            cpu0_ns = time.process_time_ns()
        sampling = TaskSampling(self._sampler, req.sample_interval_ms, t0_ns, self._log)
        sampling.start()
        try:
            result, tab, marks = await self._timed(req, t0_ns)
            # The end of the timed region: counters, the final sample, guestd's own CPU.
            end = _counters(tab)
            result.proc_samples = await sampling.stop()
            result.guestd_cpu_ms = round((time.process_time_ns() - cpu0_ns) / 1e6, 3)
        finally:
            sampling.cancel()
        result.bytes_received, result.request_count = end
        split_counters(result.steps, marks, end)
        result.sample_interval_ms = req.sample_interval_ms
        result.timing_valid = not req.screenshot_each_step
        # Outside the timed region: screenshot, then close the tab and its context.
        if tab is not None:
            result.screenshot_b64 = await self._screenshot(tab, req.task_id)
            await self._close_tab(tab, req.task_id)
        return result

    async def _timed(self, req: TaskRequest, t0_ns: int) -> Tuple[TaskResult, Optional[Tab], List[Tuple[int, int]]]:
        """The five steps. Returns the result, the tab (if one was created) and the
        (bytes, requests) counters at every step's dispatch."""
        t0_mono = t0_ns / 1e9
        task_deadline = Deadline.after_ms(req.task_timeout_ms, "task", start=t0_mono)
        steps: List[StepRecord] = []
        marks: List[Tuple[int, int]] = []
        shots: Optional[List[Dict[str, Any]]] = [] if req.screenshot_each_step else None
        tab: Optional[Tab] = None
        result: TaskResult
        current_step: Optional[str] = None
        try:
            for name in STEP_NAMES:
                current_step = name
                step_deadline = Deadline.earliest([Deadline.after_ms(req.step_timeout_ms, "step"), task_deadline])
                dispatch_ns = time.monotonic_ns() - t0_ns
                marks.append(_counters(tab))
                if name == "home":
                    tab = await self._open_tab(step_deadline)
                    settle_ns = await self._step_home(tab, req, step_deadline, t0_ns)
                elif name == "search":
                    settle_ns = await self._step_search(tab, req, step_deadline, t0_ns)
                elif name == "open_product":
                    settle_ns = await self._step_open_product(tab, req, step_deadline, t0_ns)
                elif name == "add_to_cart":
                    settle_ns = await self._step_add_to_cart(tab, req, step_deadline, t0_ns)
                else:
                    settle_ns = await self._step_verify_cart(tab, req, step_deadline, t0_ns)
                rec = StepRecord(name, dispatch_ns, settle_ns, round((settle_ns - dispatch_ns) / 1e6, 3))
                steps.append(rec)
                self._log.info("step ok", task_id=req.task_id, step=name, duration_ms=rec.duration_ms)
                if shots is not None:
                    dl = Deadline.earliest([Deadline.after(SCREENSHOT_TIMEOUT_S, "screenshot"), task_deadline])
                    shots.append({"step": name, "b64": await self._screenshot(tab, req.task_id, dl)})
            task_ms = round(steps[-1].settle_ns / 1e6, 3)
            result = TaskResult(req.task_id, True, "ok", steps, task_ms, 0, 0, None)
        except DeadlineExpired as e:
            category = "task_timeout" if e.deadline.label == "task" else "step_timeout"
            result = self._failure(req, category, current_step, str(e), steps, t0_ns)
        except TaskError as e:
            result = self._failure(req, e.category, current_step, e.message, steps, t0_ns)
        except BrowserGone as e:
            result = self._failure(req, "browser_crashed", current_step, str(e), steps, t0_ns)
        except CDPError as e:
            category = "browser_crashed" if _looks_like_crash(e) else "assertion_failed"
            result = self._failure(req, category, current_step, f"devtools: {e}", steps, t0_ns)
        result.step_screenshots = shots
        return result, tab, marks

    # -- steps ------------------------------------------------------------------------

    async def _open_tab(self, dl: Deadline) -> Tab:
        c = self._client
        ctx = (await dl.wait(c.send("Target.createBrowserContext", {"disposeOnDetach": True}), "creating browser context"))[
            "browserContextId"
        ]
        target = (
            await dl.wait(
                c.send("Target.createTarget", {"url": "about:blank", "browserContextId": ctx, "width": 1280, "height": 800}),
                "creating tab",
            )
        )["targetId"]
        cdp_session = (await dl.wait(c.send("Target.attachToTarget", {"targetId": target, "flatten": True}), "attaching tab"))[
            "sessionId"
        ]
        tab = Tab(cdp_session, target, ctx, c.subscribe(cdp_session))

        def on_event(method: str, params: Dict[str, Any]) -> None:
            if method == "Network.requestWillBeSent":
                tab.request_count += 1
            elif method == "Network.loadingFinished":
                tab.bytes_received += int(params.get("encodedDataLength") or 0)

        tab.listener = on_event
        c.add_listener(cdp_session, on_event)
        await dl.wait(c.send("Page.enable", session_id=cdp_session), "Page.enable")
        await dl.wait(c.send("Network.enable", session_id=cdp_session), "Network.enable")
        return tab

    async def _step_home(self, tab: Tab, req: TaskRequest, dl: Deadline, t0_ns: int) -> int:
        settle_ns = await self._navigate(tab, req.fixture_base_url + "/", dl, t0_ns)
        if not await self._eval(tab, _js(JS_EXISTS, sel=SEL_SEARCH_INPUT), dl):
            raise TaskError("assertion_failed", f"{SEL_SEARCH_INPUT} not found on the home page")
        return settle_ns

    async def _step_search(self, tab: Tab, req: TaskRequest, dl: Deadline, t0_ns: int) -> int:
        tab.events.drain()
        await self._eval(tab, _js(JS_SUBMIT_SEARCH, sel=SEL_SEARCH_INPUT, query=req.query), dl)
        await self._inject_fault(dl)
        await tab.events.wait_for("Page.loadEventFired", dl)
        count = await self._eval(tab, _js(JS_RESULTS_PRESENT, sel=SEL_RESULT), dl, await_promise=True)
        settle_ns = time.monotonic_ns() - t0_ns
        check = await self._eval(tab, _js(JS_RESULT_FOR_PRODUCT, sel=SEL_RESULT, pid=req.product_id), dl)
        if not check or not check.get("found"):
            raise TaskError(
                "assertion_failed",
                f"no {SEL_RESULT} with data-product-id == {req.product_id!r} among {count} result(s)",
            )
        return settle_ns

    async def _step_open_product(self, tab: Tab, req: TaskRequest, dl: Deadline, t0_ns: int) -> int:
        tab.events.drain()
        el_expr = _js(JS_EL_RESULT_FOR_PRODUCT, sel=SEL_RESULT, pid=req.product_id)
        await self._click(tab, el_expr, f"result for product {req.product_id}", dl)
        await tab.events.wait_for("Page.loadEventFired", dl)
        settle_ns = time.monotonic_ns() - t0_ns
        title = await self._eval(tab, _js(JS_TEXT, sel=SEL_PRODUCT_TITLE), dl)
        if title != req.expected_title:
            raise TaskError("assertion_failed", f"{SEL_PRODUCT_TITLE} text is {title!r}, expected {req.expected_title!r}")
        return settle_ns

    async def _step_add_to_cart(self, tab: Tab, req: TaskRequest, dl: Deadline, t0_ns: int) -> int:
        await self._eval(tab, _js(JS_INSTALL_ADDED_WATCH, sel=SEL_ADDED), dl)
        await self._click(tab, _js("document.querySelector({sel})", sel=SEL_ADD_TO_CART), SEL_ADD_TO_CART, dl)
        await self._eval(tab, JS_AWAIT_ADDED, dl, await_promise=True)
        settle_ns = time.monotonic_ns() - t0_ns
        badge = await self._eval(tab, _js(JS_CART_BADGE, sel=SEL_CART_BADGE), dl)
        if badge != 1:
            raise TaskError("assertion_failed", f"cart badge count is {badge!r}, expected 1")
        return settle_ns

    async def _step_verify_cart(self, tab: Tab, req: TaskRequest, dl: Deadline, t0_ns: int) -> int:
        settle_ns = await self._navigate(tab, req.fixture_base_url + "/cart.html", dl, t0_ns)
        items = await self._eval(tab, _js(JS_CART_ITEMS, sel=SEL_CART_ITEM, title_sel=SEL_CART_ITEM_TITLE), dl)
        items = items or []
        if len(items) != 1:
            raise TaskError("assertion_failed", f"expected exactly one {SEL_CART_ITEM}, found {len(items)}")
        item = items[0]
        if item.get("product_id") != req.product_id:
            raise TaskError("assertion_failed", f"cart item product id is {item.get('product_id')!r}, expected {req.product_id!r}")
        if item.get("title") != req.expected_title:
            raise TaskError("assertion_failed", f"cart item title is {item.get('title')!r}, expected {req.expected_title!r}")
        return settle_ns

    # -- primitives -------------------------------------------------------------------

    async def _navigate(self, tab: Tab, url: str, dl: Deadline, t0_ns: int) -> int:
        tab.events.drain()
        r = await dl.wait(self._client.send("Page.navigate", {"url": url}, session_id=tab.session_id), f"navigating to {url}")
        if r.get("errorText"):
            raise TaskError("navigation_error", f"{url}: {r['errorText']}")
        await tab.events.wait_for("Page.loadEventFired", dl)
        return time.monotonic_ns() - t0_ns

    async def _eval(self, tab: Tab, expression: str, dl: Deadline, await_promise: bool = False) -> Any:
        params = {"expression": expression, "returnByValue": True, "awaitPromise": await_promise}
        r = await dl.wait(self._client.send("Runtime.evaluate", params, session_id=tab.session_id), "evaluating in the page")
        exc = r.get("exceptionDetails")
        if exc:
            raise TaskError("assertion_failed", _describe_exception(exc))
        return (r.get("result") or {}).get("value")

    async def _click(self, tab: Tab, el_expr: str, desc: str, dl: Deadline) -> None:
        point = await self._eval(tab, _js(JS_CLICK_POINT, _raw={"el_expr": el_expr}, desc=desc), dl)
        x, y = float(point["x"]), float(point["y"])
        base = {"x": x, "y": y, "button": "left", "clickCount": 1}
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            params = dict(base, type=kind)
            if kind == "mouseMoved":
                params["button"] = "none"
                params.pop("clickCount")
            await dl.wait(self._client.send("Input.dispatchMouseEvent", params, session_id=tab.session_id), f"clicking {desc}")

    async def _inject_fault(self, dl: Deadline) -> None:
        f = self._fault
        if f is None:
            return
        if f.name == "hang_step":
            self._log.warning("fault: hanging inside step", step=FAULT_STEP)
            await dl.wait(never(), f"fault hang_step in {FAULT_STEP}")
        elif f.name == "slow_step":
            self._log.warning("fault: slow step", step=FAULT_STEP, ms=f.ms)
            await dl.wait(asyncio.sleep(f.ms / 1000.0), f"fault slow_step:{f.ms} in {FAULT_STEP}")

    async def _screenshot(self, tab: Tab, task_id: str, dl: Optional[Deadline] = None) -> Optional[str]:
        if self._client.closed:
            return None
        if dl is None:
            dl = Deadline.after(SCREENSHOT_TIMEOUT_S, "screenshot")
        try:
            r = await dl.wait(
                self._client.send("Page.captureScreenshot", {"format": "jpeg", "quality": 60}, session_id=tab.session_id),
                "capturing screenshot",
            )
            return r.get("data")
        except (DeadlineExpired, CDPError, BrowserGone) as e:
            self._log.warning("screenshot failed", task_id=task_id, error=str(e))
            return None

    async def _close_tab(self, tab: Tab, task_id: str) -> None:
        c = self._client
        c.remove_listener(tab.session_id, tab.listener)
        tab.events.close()
        if c.closed:
            return
        dl = Deadline.after(CLOSE_TIMEOUT_S, "close")
        for method, params in (
            ("Target.closeTarget", {"targetId": tab.target_id}),
            ("Target.disposeBrowserContext", {"browserContextId": tab.context_id}),
        ):
            try:
                await dl.wait(c.send(method, params), method)
            except (DeadlineExpired, CDPError, BrowserGone) as e:
                self._log.warning("tab cleanup failed", task_id=task_id, method=method, error=str(e))
                return

    def _failure(self, req: TaskRequest, category: str, step: Optional[str], error: str, steps: List[StepRecord], t0_ns: int) -> TaskResult:
        elapsed_ms = round((time.monotonic_ns() - t0_ns) / 1e6, 3)
        self._log.error("task failed", task_id=req.task_id, failure_category=category, failed_step=step, error=error, elapsed_ms=elapsed_ms)
        return TaskResult(req.task_id, False, category, steps, elapsed_ms, 0, 0, None, failed_step=step, error=error)


def _counters(tab: Optional[Tab]) -> Tuple[int, int]:
    return (tab.bytes_received, tab.request_count) if tab is not None else (0, 0)


def split_counters(steps: List[StepRecord], marks: List[Tuple[int, int]], end: Tuple[int, int]) -> None:
    """Give each step the (bytes, requests) between its dispatch mark and the next one.

    ``marks[i]`` is the cumulative count at step i's dispatch; the window of the last
    dispatched step closes at ``end``. For an ok task the steps sum exactly to ``end``.
    """
    for i, rec in enumerate(steps):
        nxt = marks[i + 1] if i + 1 < len(marks) else end
        rec.bytes_received = nxt[0] - marks[i][0]
        rec.request_count = nxt[1] - marks[i][1]


def _looks_like_crash(e: CDPError) -> bool:
    text = (e.message or "").lower()
    return any(s in text for s in ("target closed", "target crashed", "session closed", "session with given id not found", "inspected target navigated or closed"))


def _describe_exception(exc: Dict[str, Any]) -> str:
    detail = exc.get("exception") or {}
    text = detail.get("description") or detail.get("value") or exc.get("text") or "JavaScript exception"
    return str(text).splitlines()[0]
