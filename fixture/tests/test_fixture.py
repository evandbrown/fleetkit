"""Offline tests for the fixture build.

They build into a temporary directory (about seven seconds), so the checked-in
dist/ is never touched. Node and Chrome are optional: the JS ranking
cross-check is skipped without node, and nothing here needs a browser
(fixture/check.py covers the browser walk).
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import build  # noqa: E402
import check  # noqa: E402


@pytest.fixture(scope="session")
def dist(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("dist")
    build.build(out, quiet=True)
    return out


@pytest.fixture(scope="session")
def manifest(dist: Path) -> dict:
    return json.loads((dist / "manifest.json").read_text())


@pytest.fixture(scope="session")
def tasks(dist: Path) -> list[dict]:
    return json.loads((dist / "task-products.json").read_text())


@pytest.fixture(scope="session")
def products(dist: Path) -> list[dict]:
    return check.load_products(dist)


def test_catalog_has_500_unique_products(products):
    assert len(products) == 500
    assert len({p["id"] for p in products}) == 500
    assert len({p["title"] for p in products}) == 500
    for p in products:
        assert re.fullmatch(r"p\d{4}", p["id"])
        assert len(p["tags"]) == 3 and len(p["colors"]) == 4 and len(p["options"]) == 3 and len(p["specs"]) == 4


def test_catalog_js_matches_catalog_json(dist):
    js = (dist / "catalog.js").read_text()
    assert js.startswith("window.FLEETKIT_CATALOG=")
    assert json.loads(js[len("window.FLEETKIT_CATALOG="):].rstrip().rstrip(";")) == json.loads((dist / "catalog.json").read_text())


def test_task_products_shape_and_ranking(tasks, products):
    assert len(tasks) == 20
    assert len({t["product_id"] for t in tasks}) == 20
    by_id = {p["id"]: p for p in products}
    for t in tasks:
        assert set(t) == {"product_id", "query", "expected_title"}
        assert by_id[t["product_id"]]["title"] == t["expected_title"]
        cards = build.search_page_cards(products, t["query"])
        assert len(cards) == build.CARDS_PER_PAGE
        assert cards[0][0]["id"] == t["product_id"] and cards[0][1] is True
        matches, _ = build.rank_products(products, t["query"])
        assert len(matches) == 1 or matches[1][1] < matches[0][1], "the winner must not tie"


def test_task_products_cover_every_category(tasks, products):
    by_id = {p["id"]: p for p in products}
    assert {by_id[t["product_id"]]["category"] for t in tasks} == {slug for slug, _, _ in build.CATEGORIES}


def test_pages_carry_the_design_selectors(dist, tasks):
    home = (dist / "index.html").read_text()
    assert re.search(r'<input [^>]*name="q"', home)
    assert 'action="/search.html" method="get"' in home
    assert 'data-testid="cart-count">0<' in home
    search = (dist / "search.html").read_text()
    assert 'id="results"' in search and 'src="/catalog.js"' in search
    assert 'data-testid="result"' not in search, "results are rendered by app.js, not in the shell"
    cart = (dist / "cart.html").read_text()
    assert 'id="cart-items"' in cart and 'data-testid="cart-empty"' in cart
    for t in tasks:
        page = (dist / "p" / (t["product_id"] + ".html")).read_text()
        m = re.search(r'<h1 class="product-title" data-testid="product-title">(.*?)</h1>', page)
        assert m and m.group(1) == t["expected_title"]
        assert 'data-testid="add-to-cart" data-product-id="{}"'.format(t["product_id"]) in page
        assert 'data-testid="added"' not in page


def test_no_whitespace_only_text_nodes(dist):
    for name in ("index.html", "search.html", "cart.html", "p/p0001.html"):
        html = (dist / name).read_text()
        assert "\n" not in html.strip(), name


def test_manifest_envelope(manifest):
    assert manifest["catalog_size"] == 500 and manifest["task_product_count"] == 20
    for name in ("search", "product"):
        page = manifest["pages"][name]
        assert 1_000_000 <= page["bytes_total"] <= 2_000_000
        assert 1500 <= page["dom_element_count"] <= page["dom_node_count"] <= 3000
        assert 20 <= page["image_request_count"] <= 40
        assert set(page["bytes_by_class"]) >= {"document", "css", "js", "image"}
    assert 20 <= manifest["pages"]["home"]["image_request_count"] <= 40
    assert manifest["expected_bytes"] > 3_000_000
    assert manifest["expected_request_count"] > manifest["task"]["unique_request_count"]
    assert set(manifest["tolerance"]) == {"bytes_pct", "requests_pct"}
    assert set(manifest["task"]["per_product"]) == {t for t in manifest["task"]["per_product"]}
    assert len(manifest["task"]["per_product"]) == 20


def test_manifest_dom_counts_match_static_pages(dist, manifest):
    elements, nodes, images = build.count_dom((dist / "index.html").read_text())
    home = manifest["pages"]["home"]
    assert (elements, nodes, len(images)) == (home["dom_element_count"], home["dom_node_count"], home["image_request_count"])


def test_png_files_are_valid_and_sized(dist):
    png = (dist / "img" / "p" / "p0001.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert b"IHDR" in png[:33] and png.endswith(b"IEND\xaeB`\x82")
    assert 30_000 <= len(png) <= 50_000
    assert sum(1 for _ in (dist / "img" / "p").iterdir()) == 500
    assert sum(1 for _ in (dist / "img" / "g").iterdir()) == build.GALLERY_POOL


def test_build_is_deterministic(dist, tmp_path):
    other = tmp_path / "again"
    build.build(other, quiet=True)
    assert build.tree_digest(other) == build.tree_digest(dist)


def test_different_seed_changes_output(tmp_path):
    other = tmp_path / "seeded"
    build.build(other, seed=1, quiet=True)
    assert (other / "catalog.json").read_bytes() != json.dumps({}).encode()
    assert json.loads((other / "manifest.json").read_text())["seed"] == 1


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_ranking_matches_python(dist, tasks, products):
    queries = [t["query"] for t in tasks] + ["", "audio", "nordvik", "bestseller", "zzz none", "Kettle & Board"]
    result = check.node_rank(dist, queries)
    for q in queries:
        assert result[q] == [[p["id"], m] for p, m in build.search_page_cards(products, q)], q


# --- reroot.py: the public copy under a prefix -----------------------------------------------

import reroot  # noqa: E402


def test_reroot_moves_every_reference_under_the_prefix(dist, tmp_path):
    out = tmp_path / "public"
    n = reroot.reroot(dist, out, "/fleetkit-fixture")
    assert n > 1000
    home = (out / "index.html").read_text()
    assert 'action="/fleetkit-fixture/search.html"' in home
    assert 'href="/fleetkit-fixture/styles.css"' in home
    assert 'href="/"' not in home and 'src="/img' not in home
    app_js = (out / "app.js").read_text()
    assert 'href=\\"/fleetkit-fixture/p/{id}.html\\"' in app_js
    catalog = json.loads((out / "catalog.json").read_text())
    assert all(p["image"].startswith("/fleetkit-fixture/img/p/") for p in catalog["products"])
    # The source, the images and the manifest are untouched.
    assert (dist / "index.html").read_bytes() != home.encode()
    assert (out / "manifest.json").read_bytes() == (dist / "manifest.json").read_bytes()
    assert (out / "img" / "hero.png").read_bytes() == (dist / "img" / "hero.png").read_bytes()


def test_reroot_refuses_a_dangling_reference(dist, tmp_path):
    out = tmp_path / "public"
    reroot.reroot(dist, out, "fleetkit-fixture/")
    (out / "styles.css").unlink()
    with pytest.raises(reroot.RerootError, match="styles.css"):
        reroot.check(out, "/fleetkit-fixture")
    with pytest.raises(reroot.RerootError):
        reroot.normalise("/")
