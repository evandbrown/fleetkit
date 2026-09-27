"""Pure-Python units: faults, deadlines, the log ring, the request schema, the site."""

from __future__ import annotations

import asyncio
import io
import json

import pytest

from guestd.clock import Deadline, DeadlineExpired, never
from guestd.faults import Fault, parse_fault
from guestd.log import LogRing
from guestd.selftest import HOME_PAGE_BYTES, SiteServer
from guestd.task import FAILURE_CATEGORIES, STEP_NAMES, TaskRequest


def test_parse_fault_closed_set():
    assert parse_fault(None) is None
    assert parse_fault("") is None
    assert parse_fault("none") is None
    assert parse_fault("crash_on_start") == Fault("crash_on_start")
    assert parse_fault("slow_step:2500") == Fault("slow_step", 2500)
    assert str(parse_fault("slow_step:2500")) == "slow_step:2500"
    for bad in ("nope", "slow_step", "slow_step:abc", "hang_task:1", "slow_step:-1"):
        with pytest.raises(ValueError):
            parse_fault(bad)


def test_deadline_earliest_prefers_task_on_tie():
    now = 100.0
    step = Deadline(now + 5, "step", 5)
    task = Deadline(now + 5, "task", 5)
    assert Deadline.earliest([step, task]).label == "task"
    assert Deadline.earliest([task, step]).label == "task"
    sooner = Deadline(now + 1, "step", 1)
    assert Deadline.earliest([sooner, task]) is sooner


def test_deadline_wait_expires_with_label():
    async def go():
        dl = Deadline.after_ms(50, "step")
        with pytest.raises(DeadlineExpired) as ei:
            await dl.wait(never(), "testing")
        assert ei.value.deadline.label == "step"
        assert "50 ms" in str(ei.value) and "testing" in str(ei.value)
        quick = Deadline.after_ms(1000, "task")

        async def value():
            return 42

        assert await quick.wait(value()) == 42

    asyncio.run(go())


def test_log_ring_seq_since_tail():
    out = io.StringIO()
    ring = LogRing(capacity=3, stream=out)
    for i in range(5):
        ring.info("m", i=i)
    assert ring.seq == 5
    assert [r["seq"] for r in ring.since(0)] == [3, 4, 5]
    assert [r["seq"] for r in ring.since(4)] == [5]
    assert ring.since(5) == []
    assert [r["i"] for r in ring.tail(2)] == [3, 4]
    lines = [json.loads(l) for l in out.getvalue().splitlines()]
    assert len(lines) == 5 and lines[0]["component"] == "guestd" and lines[0]["level"] == "info"


def test_task_request_schema_and_defaults():
    req = TaskRequest.from_dict(
        {"task_id": "t", "fixture_base_url": "http://fixture/", "product_id": "p", "query": "q", "expected_title": "T"}
    )
    assert req.fixture_base_url == "http://fixture"
    assert (req.step_timeout_ms, req.task_timeout_ms) == (10000, 45000)
    req2 = TaskRequest.from_dict(
        {"task_id": "t", "fixture_base_url": "http://f", "product_id": "p", "query": "q", "expected_title": "T", "step_timeout_ms": 1500.0, "task_timeout_ms": 9000}
    )
    assert (req2.step_timeout_ms, req2.task_timeout_ms) == (1500, 9000)
    for bad in (
        None,
        [],
        {"task_id": "t"},
        {"task_id": "t", "fixture_base_url": "ftp://x", "product_id": "p", "query": "q", "expected_title": "T"},
        {"task_id": "t", "fixture_base_url": "http://f", "product_id": "p", "query": "q", "expected_title": "T", "step_timeout_ms": 0},
        {"task_id": "t", "fixture_base_url": "http://f", "product_id": "p", "query": "q", "expected_title": "T", "task_timeout_ms": "x"},
    ):
        with pytest.raises(ValueError):
            TaskRequest.from_dict(bad)


def test_closed_sets_match_design():
    assert STEP_NAMES == ("home", "search", "open_product", "add_to_cart", "verify_cart")
    assert set(FAILURE_CATEGORIES) == {
        "ok", "step_timeout", "task_timeout", "assertion_failed", "navigation_error", "browser_crashed", "guest_unreachable", "session_not_ready",
    }


def test_selftest_site_has_the_design_selectors():
    site = SiteServer()
    home, ctype = site.render("/")
    assert ctype.startswith("text/html") and len(home) >= HOME_PAGE_BYTES
    text = home.decode()
    assert '<input name="q"' in text and 'action="/search.html"' in text and 'data-testid="cart-count"' in text
    product = site.render("/p/sku-1001.html")[0].decode()
    assert '<h1 data-testid="product-title">Brass Desk Lamp</h1>' in product
    assert 'data-testid="add-to-cart"' in product and 'data-testid="added"' in product
    assert site.render("/p/nope.html") is None and site.render("/etc/passwd") is None
    app = site.render("/app.js")[0].decode()
    for marker in ("'data-testid', 'result'", "'data-product-id'", "'cart-item'", "'cart-item-title'"):
        assert marker in app
    cart = site.render("/cart.html")[0].decode()
    assert 'data-testid="cart-items"' in cart


def test_selftest_site_serves_over_http():
    async def go():
        site = SiteServer()
        host, port = await site.start("127.0.0.1", 0)
        try:
            from guestd import minihttp

            status, headers, body = await minihttp.request(host, port, "GET", "/")
            assert status == 200 and len(body) >= HOME_PAGE_BYTES and headers["connection"] == "close"
            status, _, body = await minihttp.request(host, port, "GET", "/products.json")
            assert status == 200 and json.loads(body)[0]["id"] == "sku-1001"
            status, _, _ = await minihttp.request(host, port, "GET", "/missing")
            assert status == 404
            status, _, _ = await minihttp.request(host, port, "POST", "/", b"x")
            assert status == 405
        finally:
            await site.stop()

    asyncio.run(go())
