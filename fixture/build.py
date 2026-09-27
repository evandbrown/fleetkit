#!/usr/bin/env python3
"""Build the fleetkit fixture site into dist/, deterministically.

The fixture is the static shopping site the harness task runs against
(docs/harness-design.md, sections 1 and 4). Everything under dist/ is a pure
function of the seed: the same seed gives byte-identical output on any
machine, so page weights, request counts and DOM sizes in dist/manifest.json
are stable numbers the report can hold a run against.

Standard library only. Python 3.9+ (uses random.Random.randbytes).

    python3 fixture/build.py [--out fixture/dist] [--seed 20260926]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import struct
import sys
import zlib
from html import escape
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "src"

DEFAULT_SEED = 20260926
CATALOG_SIZE = 500
TASK_PRODUCTS = 20
CARDS_PER_PAGE = 24          # result cards on search; featured on home; related on product
CART_RECOMMENDATIONS = 12
GALLERY_SHOTS = 5            # pool images per product page, plus the product's own image
GALLERY_POOL = 64
COLLECTION_TILES = 8         # strip on the search page
CATEGORY_TILES = 8           # strip on the home page
ADD_TO_CART_DELAY_MS = 100   # the simulated cart API latency before [data-testid=added] appears

# Image shapes. Every image is an 8-bit indexed PNG whose pixels carry 4 bits of
# entropy each, so a WxH image is close to W*H/2 bytes after deflate (see
# make_image). These sizes put search and product pages at 1.2-1.6 MB.
MAIN_IMAGE = (320, 240)      # one per product: card thumbnail and gallery lead
GALLERY_IMAGE = (480, 360)   # shared pool of detail shots
TILE_IMAGE = (320, 240)      # category and collection tiles
HERO_IMAGE = (640, 240)

# Self-checks. The build fails if the generated site drifts out of the design's
# envelope, so nobody measures against a fixture that quietly changed shape.
PAGE_BYTES_RANGE = (1_000_000, 2_000_000)
DOM_RANGE = (1500, 3000)
IMAGE_REQUESTS_RANGE = (20, 40)

CATEGORIES = [
    ("audio", "Audio", ["Headphones", "Bluetooth Speaker", "Earbuds", "Turntable", "Soundbar", "Microphone"]),
    ("kitchen", "Kitchen", ["Chef Knife", "Skillet", "Kettle", "Coffee Grinder", "Dutch Oven", "Cutting Board"]),
    ("outdoor", "Outdoor", ["Tent", "Sleeping Bag", "Headlamp", "Trekking Poles", "Camp Stove", "Daypack"]),
    ("fitness", "Fitness", ["Running Shoes", "Yoga Mat", "Kettlebell", "Jump Rope", "Resistance Bands", "Foam Roller"]),
    ("office", "Home Office", ["Desk Lamp", "Monitor Arm", "Keyboard", "Office Chair", "Notebook", "Webcam"]),
    ("lighting", "Lighting", ["Floor Lamp", "Pendant Light", "Table Lamp", "String Lights", "Wall Sconce", "Lantern"]),
    ("travel", "Travel", ["Carry-on Suitcase", "Packing Cubes", "Travel Pillow", "Passport Wallet", "Duffel Bag", "Luggage Scale"]),
    ("toys", "Toys & Games", ["Board Game", "Jigsaw Puzzle", "Building Blocks", "Plush Bear", "Stunt Kite", "Card Game"]),
]
BRANDS = ["Nordvik", "Ashgrove", "Pellucid", "Kestrel", "Marlow", "Tessellate", "Ondo", "Halvard",
          "Quill", "Ridgeline", "Solenne", "Bramble", "Cobalt", "Fennmoor", "Larkspur", "Vantage"]
ADJECTIVES = ["Trail", "Compact", "Classic", "Ultralight", "Studio", "Weekend", "Everyday", "Alpine",
              "Urban", "Heritage", "Slim", "Wireless", "Insulated", "Foldable", "Modular", "Ceramic",
              "Bamboo", "Merino", "Recycled", "Quiet"]
VARIANTS = ["", "", "", "Mk II", "Pro", "Lite", "XL", "Mini", "Edition 2", "Plus"]
TAGS = ["bestseller", "new", "gift", "eco", "sale", "staff pick", "limited", "restocked", "premium", "value"]
COLORS = ["#1f2933", "#c8102e", "#f2c14e", "#2a9d8f", "#264653", "#e76f51", "#8d99ae", "#6a4c93",
          "#ffffff", "#3a5a40", "#ffb703", "#219ebc"]
MATERIALS = ["aluminium", "recycled nylon", "walnut", "stainless steel", "merino wool", "bamboo",
             "cast iron", "silicone", "vegan leather", "ripstop", "ceramic", "beech"]
PERKS = ["Free shipping", "30-day returns", "2-year warranty", "Ships in 24h", "Carbon neutral", "Gift wrap"]
BLURBS = [
    "Built for daily use, with the details that matter and nothing that does not.",
    "A quiet favourite in our workshop: tough, light, and easy to live with.",
    "Made in small batches from materials we can trace back to the source.",
    "Designed with feedback from hundreds of customers, then tested for a year.",
    "The version we kept reaching for. Now with a longer warranty.",
    "Simple to set up, simpler to maintain, and priced like it should be.",
    "Weighs less than you expect and lasts longer than you would guess.",
    "Ships flat, assembles in minutes, and looks good doing it.",
]
REVIEWERS = ["A. R.", "J. K.", "M. T.", "S. O.", "P. L.", "D. W.", "E. N.", "R. B."]
REVIEW_TEXT = [
    "Exactly as described. Arrived two days early and packaged well.",
    "Solid build quality. I use it every day and it still looks new.",
    "Good value. Setup took five minutes with the printed guide.",
    "Replaced an older model; this one is quieter and lighter.",
    "Would buy again. The colour matches the photos.",
    "Took a star off for the manual, but the product itself is great.",
]

# Categories map to a silhouette drawn into every image of that category.
SHAPES = {"audio": "circle", "kitchen": "bottle", "outdoor": "triangle", "fitness": "diamond",
          "office": "rect", "lighting": "bulb", "travel": "rect", "toys": "circle"}

# ---------------------------------------------------------------------------
# Search ranking: the same algorithm is implemented in src/app.js (rankProducts).
# Keep the two in sync; build.py picks task queries with this version and
# check.py confirms the JS version agrees in a real browser.
# ---------------------------------------------------------------------------

TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def tokens(text: str) -> list[str]:
    return [t for t in TOKEN_SPLIT.split(text.lower()) if t]


def index_product(p: dict) -> dict:
    return {
        "title": set(tokens(p["title"])),
        "brand": set(tokens(p["brand"])),
        "category": set(tokens(p["category"]) + tokens(p["category_name"])),
        "tags": set(t for tag in p["tags"] for t in tokens(tag)),
    }


def score_product(idx: dict, qtokens: list[str]) -> int:
    total = 0
    for t in qtokens:
        s = 0
        if t in idx["title"]:
            s += 5
        if t in idx["brand"]:
            s += 3
        if t in idx["category"]:
            s += 2
        if t in idx["tags"]:
            s += 1
        if s == 0:
            return 0  # every query token must match somewhere
        total += s
    return total


def rank_products(products: list[dict], query: str) -> tuple[list[tuple[dict, int]], list[dict]]:
    """Return (matches sorted by score desc then id, filler in rotated id order)."""
    qtokens = tokens(query)
    matches: list[tuple[dict, int]] = []
    if qtokens:
        for p in products:
            s = score_product(p["_idx"], qtokens)
            if s > 0:
                matches.append((p, s))
        matches.sort(key=lambda ps: (-ps[1], ps[0]["id"]))
    matched = {p["id"] for p, _ in matches}
    offset = sum(ord(c) for c in query) % len(products)
    rotated = products[offset:] + products[:offset]
    filler = [p for p in rotated if p["id"] not in matched]
    return matches, filler


def search_page_cards(products: list[dict], query: str) -> list[tuple[dict, bool]]:
    matches, filler = rank_products(products, query)
    cards = [(p, True) for p, _ in matches[:CARDS_PER_PAGE]]
    for p in filler:
        if len(cards) >= CARDS_PER_PAGE:
            break
        cards.append((p, False))
    return cards


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def money(cents: int) -> str:
    return "${:,.2f}".format(cents / 100)


def build_catalog(rng: random.Random) -> list[dict]:
    combos = [(b, a, ci, n) for b in BRANDS for a in ADJECTIVES
              for ci, (_, _, nouns) in enumerate(CATEGORIES) for n in nouns]
    rng.shuffle(combos)
    products = []
    seen_titles = set()
    for i, (brand, adj, ci, noun) in enumerate(combos[:CATALOG_SIZE]):
        slug, cat_name, _ = CATEGORIES[ci]
        pid = "p{:04d}".format(i + 1)
        variant = rng.choice(VARIANTS)
        title = " ".join(x for x in (brand, adj, noun, variant) if x)
        while title in seen_titles:
            title += " II"
        seen_titles.add(title)
        cents = rng.choice([999, 1499, 1999, 2450, 2900, 3495, 3999, 4900, 5900, 6950, 7900,
                            8900, 9900, 11900, 12900, 14900, 17900, 19900, 24900, 29900, 34900, 49900])
        rating = round(rng.uniform(3.2, 5.0), 1)
        reviews = rng.randint(3, 2400)
        tags = rng.sample(TAGS, 3)
        colors = rng.sample(COLORS, 4)
        options = rng.sample(["S", "M", "L", "XL", "One size", "EU 40", "EU 42", "EU 44", "1 L", "2 L",
                              "Single", "Twin", "Left", "Right", "Matte", "Gloss"], 5)
        weight_g = rng.choice([120, 240, 380, 450, 620, 800, 1100, 1450, 2200, 3100])
        specs = [
            ["Weight", "{} g".format(weight_g)],
            ["Material", rng.choice(MATERIALS)],
            ["Colour", rng.choice(["Graphite", "Sand", "Forest", "Oxblood", "Slate", "Ivory", "Cobalt"])],
            ["Dimensions", "{} x {} x {} cm".format(rng.randint(8, 60), rng.randint(6, 40), rng.randint(2, 30))],
            ["Warranty", rng.choice(["1 year", "2 years", "3 years", "Lifetime"])],
            ["Model", "{}-{}".format(brand[:3].upper(), rng.randint(100, 999))],
        ]
        gallery = rng.sample(range(GALLERY_POOL), GALLERY_SHOTS)
        products.append({
            "id": pid,
            "title": title,
            "brand": brand,
            "category": slug,
            "category_name": cat_name,
            "noun": noun,
            "adjective": adj,
            "price_cents": cents,
            "price": money(cents),
            "rating": rating,
            "reviews": reviews,
            "tags": tags,
            "colors": colors,
            "options": options,
            "specs": specs,
            "blurb": rng.choice(BLURBS),
            "image": "/img/p/{}.png".format(pid),
            "gallery": ["/img/g/{:02d}.png".format(g) for g in gallery],
            "perks": rng.sample(PERKS, 3),
            "_image_seed": rng.getrandbits(32),
            "_review_seed": rng.getrandbits(32),
        })
    for p in products:
        p["_idx"] = index_product(p)
    return products


def public_catalog(products: list[dict]) -> list[dict]:
    """The fields shipped to the browser in catalog.json / catalog.js."""
    keys = ["id", "title", "brand", "category", "category_name", "price", "rating", "reviews",
            "tags", "colors", "blurb", "image", "perks"]
    out = []
    for p in products:
        d = {k: p[k] for k in keys}
        d["options"] = p["options"][:3]   # cards show three options and four specs;
        d["specs"] = p["specs"][:4]       # the static product page has the full set
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Images: tiny pure-Python indexed PNG writer plus a fast procedural generator
# ---------------------------------------------------------------------------

def png_chunk(kind: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def png_indexed(width: int, height: int, palette: list[tuple[int, int, int]], rows: list[bytes]) -> bytes:
    raw = b"".join(b"\x00" + row for row in rows)  # filter type 0 on every scanline
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0)
    plte = b"".join(bytes(c) for c in palette)
    return (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", ihdr) + png_chunk(b"PLTE", plte)
            + png_chunk(b"IDAT", zlib.compress(raw, 6)) + png_chunk(b"IEND", b""))


def hex_rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def gradient(a: tuple[int, int, int], b: tuple[int, int, int], n: int) -> list[tuple[int, int, int]]:
    return [tuple(int(a[k] + (b[k] - a[k]) * i / (n - 1)) for k in range(3)) for i in range(n)]


def shape_extents(shape: str, width: int, height: int, y: int) -> tuple[int, int]:
    """Left and right edge of the silhouette on row y (x0 >= x1 means none)."""
    cx, cy = width // 2, height // 2
    rx, ry = int(width * 0.28), int(height * 0.36)
    if shape == "circle":
        d = (y - cy) / ry
        if abs(d) >= 1:
            return 0, 0
        half = int(rx * (1 - d * d) ** 0.5)
        return cx - half, cx + half
    if shape == "rect":
        if abs(y - cy) > ry:
            return 0, 0
        return cx - rx, cx + rx
    if shape == "triangle":
        top, bottom = cy - ry, cy + ry
        if y < top or y > bottom:
            return 0, 0
        half = int(rx * (y - top) / (bottom - top))
        return cx - half, cx + half
    if shape == "diamond":
        d = abs(y - cy) / ry
        if d >= 1:
            return 0, 0
        half = int(rx * (1 - d))
        return cx - half, cx + half
    if shape == "bottle":
        top, bottom = cy - ry, cy + ry
        if y < top or y > bottom:
            return 0, 0
        neck = top + (bottom - top) // 3
        half = rx // 3 if y < neck else rx
        return cx - half, cx + half
    if shape == "bulb":
        d = (y - (cy - ry // 3)) / (ry * 0.7)
        if abs(d) < 1:
            half = int(rx * (1 - d * d) ** 0.5)
            return cx - half, cx + half
        if cy + ry // 3 <= y <= cy + ry:
            return cx - rx // 4, cx + rx // 4
        return 0, 0
    return 0, 0


def make_image(seed: int, width: int, height: int, bg: str, fg: str, shape: str) -> bytes:
    """A textured background gradient with a category silhouette on top.

    Each pixel is a uniformly random one of 8 palette indices inside an
    8-wide window that slides down a 32-step gradient, so deflate can do no
    better than 3 bits per pixel and in practice lands near 4.4 (the window
    slides inside each deflate block): a 320x240 image is about 34 KB, which
    is what makes page weight predictable. randbytes plus
    bytes.translate keep the whole thing in C, so 600 images build in seconds.
    """
    rng = random.Random(seed)
    palette = gradient(hex_rgb(bg), (245, 241, 234), 32) + gradient(hex_rgb(fg), (250, 250, 250), 32)
    noise = rng.randbytes(width * height)
    rows = []
    for y in range(height):
        w_bg = 24 * y // height
        w_fg = 32 + 24 - 24 * y // height
        t_bg = bytes(w_bg + (b >> 5) for b in range(256))
        t_fg = bytes(w_fg + (b >> 5) for b in range(256))
        row = noise[y * width:(y + 1) * width]
        x0, x1 = shape_extents(shape, width, height, y)
        if x1 > x0:
            rows.append(row[:x0].translate(t_bg) + row[x0:x1].translate(t_fg) + row[x1:].translate(t_bg))
        else:
            rows.append(row.translate(t_bg))
    return png_indexed(width, height, palette, rows)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

# One card template shared by Python (static pages) and app.js (search results,
# cart recommendations are static too). app.js receives it verbatim; both sides
# substitute {field} with an HTML-escaped value. Keep it free of newlines
# between text and tags: the build strips indentation, not inline spaces.
CARD_TEMPLATE = """<a class="card{extra_class}" data-testid="{testid}" data-product-id="{id}" href="/p/{id}.html">
<figure class="card-media"><img src="{image}" alt="{title}" width="320" height="240"><figcaption class="card-badges"><span class="badge">{tag1}</span><span class="badge">{tag2}</span><span class="badge badge-cat">{category_name}</span></figcaption></figure>
<div class="card-body">
<p class="card-brand">{brand}</p>
<h3 class="card-title">{title}</h3>
<div class="card-rating" aria-label="{rating} out of 5"><i class="star {s1}"></i><i class="star {s2}"></i><i class="star {s3}"></i><i class="star {s4}"></i><i class="star {s5}"></i><span class="card-reviews">{reviews} reviews</span></div>
<ul class="card-swatches"><li class="swatch" style="background:{c1}"></li><li class="swatch" style="background:{c2}"></li><li class="swatch" style="background:{c3}"></li><li class="swatch" style="background:{c4}"></li></ul>
<ul class="card-options"><li>{o1}</li><li>{o2}</li><li>{o3}</li></ul>
<dl class="card-specs"><div><dt>{k1}</dt><dd>{v1}</dd></div><div><dt>{k2}</dt><dd>{v2}</dd></div><div><dt>{k3}</dt><dd>{v3}</dd></div><div><dt>{k4}</dt><dd>{v4}</dd></div></dl>
<ul class="card-perks"><li><i class="ico"></i><span>{perk1}</span></li><li><i class="ico"></i><span>{perk2}</span></li><li><i class="ico"></i><span>{perk3}</span></li></ul>
<p class="card-blurb">{blurb}</p>
<div class="card-footer"><span class="card-price">{price}</span><span class="card-cta">View</span></div>
</div>
</a>"""

FIELD_RE = re.compile(r"\{(\w+)\}")


def minify(html: str) -> str:
    """Drop newlines and the indentation after them, so no whitespace-only text nodes exist."""
    return re.sub(r"\n\s*", "", html)


def card_fields(p: dict, testid: str, extra_class: str = "") -> dict:
    f = {"id": p["id"], "title": p["title"], "brand": p["brand"], "image": p["image"],
         "category_name": p["category_name"], "price": p["price"], "rating": p["rating"],
         "reviews": p["reviews"], "blurb": p["blurb"], "testid": testid, "extra_class": extra_class}
    f["tag1"], f["tag2"] = p["tags"][0], p["tags"][1]
    full = int(round(p["rating"]))
    for i in range(5):
        f["s{}".format(i + 1)] = "on" if i < full else "off"
    for i in range(4):
        f["c{}".format(i + 1)] = p["colors"][i]
    for i in range(3):
        f["o{}".format(i + 1)] = p["options"][i]
    for i in range(4):
        f["k{}".format(i + 1)], f["v{}".format(i + 1)] = p["specs"][i]
    for i in range(3):
        f["perk{}".format(i + 1)] = p["perks"][i]
    return f


def render_template(template: str, fields: dict) -> str:
    return FIELD_RE.sub(lambda m: escape(str(fields[m.group(1)]), quote=True), template)


def card_html(p: dict, testid: str = "product-card", extra_class: str = "") -> str:
    return render_template(minify(CARD_TEMPLATE), card_fields(p, testid, extra_class))


def header_html(query: str = "") -> str:
    nav = "".join('<li><a href="/search.html?q={}">{}</a></li>'.format(slug, escape(name))
                  for slug, name, _ in CATEGORIES[:6])
    return minify("""<header class="site-header">
<div class="wrap">
<a class="brand" href="/"><span class="brand-mark"></span><span class="brand-name">Fleetkit Outfitters</span></a>
<nav class="site-nav" aria-label="Departments"><ul>{nav}</ul></nav>
<form class="search" action="/search.html" method="get" role="search">
<label class="visually-hidden" for="q">Search products</label>
<input id="q" type="search" name="q" placeholder="Search products" autocomplete="off" value="{query}">
<button type="submit">Search</button>
</form>
<a class="cart-link" href="/cart.html" aria-label="Cart"><span class="cart-icon"></span><span class="cart-count" data-testid="cart-count">0</span></a>
</div>
</header>""").format(nav=nav, query=escape(query, quote=True))


def footer_html() -> str:
    cols = [
        ("Shop", [name for _, name, _ in CATEGORIES[:6]]),
        ("Help", ["Shipping", "Returns", "Warranty", "Size guide", "Contact", "FAQ"]),
        ("Company", ["About", "Sustainability", "Careers", "Press", "Wholesale", "Affiliates"]),
        ("Legal", ["Privacy", "Terms", "Cookies", "Accessibility", "Imprint", "Licences"]),
    ]
    col_html = "".join(
        '<div class="footer-col"><h4>{}</h4><ul>{}</ul></div>'.format(
            escape(title), "".join('<li><a href="/search.html?q={0}">{1}</a></li>'.format(
                escape(item.lower().replace(" ", "+")), escape(item)) for item in items))
        for title, items in cols)
    return minify("""<footer class="site-footer">
<div class="wrap">
<div class="footer-grid">{cols}</div>
<form class="newsletter" action="/search.html" method="get"><label for="nl">Newsletter</label><input id="nl" type="text" name="q" placeholder="Search instead"><button type="submit">Go</button></form>
<p class="footer-note">Fleetkit Outfitters is a generated test fixture. Products, brands and reviews are fictional.</p>
</div>
</footer>""").format(cols=col_html)


def page_html(title: str, body: str, page_id: str, query: str = "", with_catalog: bool = False) -> str:
    catalog_tag = '<script src="/catalog.js"></script>' if with_catalog else ""
    return minify("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/styles.css">
{catalog_tag}
</head>
<body data-page="{page_id}">
{header}
<main class="wrap" id="main">
{body}
</main>
{footer}
<script src="/app.js"></script>
</body>
</html>""").format(title=escape(title), catalog_tag=catalog_tag, page_id=page_id,
                   header=header_html(query), body=body, footer=footer_html())


def stars_html(rating: float) -> str:
    full = int(round(rating))
    return "".join('<i class="star {}"></i>'.format("on" if i < full else "off") for i in range(5))


def home_body(products: list[dict]) -> str:
    featured = sorted(products, key=lambda p: (-p["rating"], -p["reviews"], p["id"]))[:CARDS_PER_PAGE]
    tiles = "".join(
        '<li><a class="tile" href="/search.html?q={slug}"><img src="/img/c/{slug}.png" alt="" width="320" height="240"><span class="tile-label">{name}</span></a></li>'
        .format(slug=slug, name=escape(name)) for slug, name, _ in CATEGORIES[:CATEGORY_TILES])
    cards = "".join(card_html(p, "featured") for p in featured)
    return minify("""<section class="hero">
<img class="hero-image" src="/img/hero.png" alt="" width="640" height="240">
<div class="hero-copy"><p class="eyebrow">Autumn collection</p><h1>Gear that earns its place</h1><p>Search {count} products across {cats} departments. Free shipping on every order.</p><a class="button" href="/search.html?q=bestseller">Shop bestsellers</a></div>
</section>
<section class="categories" aria-labelledby="cat-h"><h2 id="cat-h">Departments</h2><ul class="tile-grid">{tiles}</ul></section>
<section class="featured" aria-labelledby="feat-h"><div class="section-head"><h2 id="feat-h">Top rated</h2><a href="/search.html?q=bestseller">See all</a></div><div class="card-grid">{cards}</div></section>""").format(
        count=len(products), cats=len(CATEGORIES), tiles=tiles, cards=cards)


def filters_html(products: list[dict]) -> str:
    def group(legend: str, name: str, options: list[tuple[str, str, int]]) -> str:
        items = "".join(
            '<label class="filter-opt"><input type="checkbox" name="{}" value="{}"><span class="filter-name">{}</span><span class="filter-count">{}</span></label>'
            .format(name, escape(value), escape(label), count) for value, label, count in options)
        return '<fieldset class="filter-group"><legend>{}</legend>{}</fieldset>'.format(escape(legend), items)

    cat_counts = {slug: sum(1 for p in products if p["category"] == slug) for slug, _, _ in CATEGORIES}
    brand_counts = {b: sum(1 for p in products if p["brand"] == b) for b in BRANDS}
    tag_counts = {t: sum(1 for p in products if t in p["tags"]) for t in TAGS}
    price_bands = [("0-25", "Under $25", 25_00), ("25-50", "$25 to $50", 50_00), ("50-100", "$50 to $100", 100_00),
                   ("100-200", "$100 to $200", 200_00), ("200-350", "$200 to $350", 350_00),
                   ("350-500", "$350 to $500", 500_00)]
    lower = 0
    price_opts = []
    for value, label, upper in price_bands:
        price_opts.append((value, label, sum(1 for p in products if lower <= p["price_cents"] < upper)))
        lower = upper
    rating_opts = [("{}".format(r), "{} stars & up".format(r), sum(1 for p in products if p["rating"] >= r))
                   for r in (4.5, 4, 3.5, 3)]
    groups = [
        group("Department", "category", [(slug, name, cat_counts[slug]) for slug, name, _ in CATEGORIES]),
        group("Brand", "brand", [(b.lower(), b, brand_counts[b]) for b in BRANDS[:8]]),
        group("Price", "price", price_opts),
        group("Rating", "rating", rating_opts),
        group("Tag", "tag", [(t, t.capitalize(), tag_counts[t]) for t in TAGS[:8]]),
    ]
    return '<aside class="filters" aria-label="Filters"><h2>Refine</h2>{}<button type="button" class="button button-ghost">Clear filters</button></aside>'.format("".join(groups))


def collections_html(rng: random.Random) -> str:
    names = ["Cabin weekend", "Desk reset", "Trail season", "Small kitchens", "Gifts under $50",
             "Quiet mornings", "Carry-on only", "Rainy days"]
    return '<section class="collections" aria-labelledby="col-h"><h2 id="col-h">Collections</h2><ul class="tile-grid tile-grid-small">{}</ul></section>'.format(
        "".join('<li><a class="tile" href="/search.html?q={q}"><img src="/img/s/{i:02d}.png" alt="" width="320" height="240"><span class="tile-label">{name}</span></a></li>'
                .format(q=escape(name.split()[0].lower()), i=i, name=escape(name)) for i, name in enumerate(names)))


def search_body(products: list[dict], rendered_cards: str = "", count_text: str = "") -> str:
    pages = "".join('<li><a href="/search.html?q=" class="page{}">{}</a></li>'.format(" current" if i == 1 else "", i)
                    for i in range(1, 9))
    return minify("""<nav class="breadcrumb" aria-label="Breadcrumb"><ol><li><a href="/">Home</a></li><li><a href="/search.html?q=">Search</a></li><li aria-current="page">Results</li></ol></nav>
<div class="search-layout">
{filters}
<section class="results" aria-labelledby="res-h">
<div class="section-head"><h2 id="res-h">Results</h2><p class="result-count" data-testid="result-count">{count_text}</p><label class="sort">Sort by <select name="sort"><option>Relevance</option><option>Price: low to high</option><option>Price: high to low</option><option>Rating</option></select></label></div>
<div class="card-grid" id="results">{cards}</div>
<nav class="pagination" aria-label="Pagination"><ul>{pages}</ul></nav>
</section>
</div>
{collections}""").format(filters=filters_html(products), count_text=escape(count_text),
                          cards=rendered_cards, pages=pages,
                          collections=collections_html(random.Random(0)))


def reviews_html(p: dict) -> str:
    rng = random.Random(p["_review_seed"])
    out = []
    for i in range(6):
        rating = rng.randint(3, 5)
        out.append('<article class="review"><header><h4>{who}</h4><span class="review-stars" aria-label="{r} out of 5">{stars}</span><time datetime="2026-0{m}-{d:02d}">2026-0{m}-{d:02d}</time></header><p>{text}</p></article>'.format(
            who=escape(rng.choice(REVIEWERS)), r=rating, stars=stars_html(rating), m=rng.randint(1, 9),
            d=rng.randint(1, 28), text=escape(rng.choice(REVIEW_TEXT))))
    return '<section class="reviews" aria-labelledby="rev-h"><h2 id="rev-h">Reviews</h2>{}</section>'.format("".join(out))


def related_products(products: list[dict], p: dict) -> list[dict]:
    same = [q for q in products if q["category"] == p["category"] and q["id"] != p["id"]]
    start = same.index(min(same, key=lambda q: (abs(int(q["id"][1:]) - int(p["id"][1:])), q["id"])))
    rotated = same[start:] + same[:start]
    return rotated[:CARDS_PER_PAGE]


def product_body(products: list[dict], p: dict) -> str:
    thumbs = "".join(
        '<li><button type="button" class="thumb" data-src="{src}"><img src="{src}" alt="" width="{w}" height="{h}"></button></li>'
        .format(src=src, w=w, h=h) for src, (w, h) in [(p["image"], MAIN_IMAGE)] + [(g, GALLERY_IMAGE) for g in p["gallery"]])
    swatches = "".join('<li class="swatch" style="background:{}" title="{}"></li>'.format(c, c) for c in p["colors"])
    options = "".join('<li><button type="button" class="chip{}">{}</button></li>'.format(" selected" if i == 0 else "", escape(o))
                      for i, o in enumerate(p["options"]))
    specs = "".join("<tr><th scope=\"row\">{}</th><td>{}</td></tr>".format(escape(k), escape(v)) for k, v in p["specs"])
    perks = "".join('<li><i class="ico"></i><span>{}</span></li>'.format(escape(x)) for x in p["perks"])
    related = "".join(card_html(q, "related") for q in related_products(products, p))
    body = minify("""<nav class="breadcrumb" aria-label="Breadcrumb"><ol><li><a href="/">Home</a></li><li><a href="/search.html?q={cat}">{cat_name}</a></li><li aria-current="page">{title}</li></ol></nav>
<article class="product" data-product-id="{id}">
<section class="gallery" aria-label="Gallery">
<figure class="gallery-main"><img id="gallery-main" src="{image}" alt="{title}" width="320" height="240"></figure>
<ul class="gallery-thumbs">{thumbs}</ul>
</section>
<section class="product-info">
<p class="card-brand">{brand}</p>
<h1 class="product-title" data-testid="product-title">{title}</h1>
<div class="card-rating" aria-label="{rating} out of 5">{stars}<span class="card-reviews">{reviews} reviews</span></div>
<p class="product-price" data-testid="product-price">{price}</p>
<h3 class="option-label">Colour</h3>
<ul class="card-swatches product-swatches">{swatches}</ul>
<h3 class="option-label">Option</h3>
<ul class="chips">{options}</ul>
<div class="buy">
<label class="qty-label" for="qty">Quantity</label><input id="qty" class="qty" type="number" name="qty" min="1" max="9" value="1">
<button type="button" class="button button-primary" data-testid="add-to-cart" data-product-id="{id}">Add to cart</button>
</div>
<ul class="card-perks product-perks">{perks}</ul>
<p class="product-blurb">{blurb}</p>
<p class="product-blurb">The {noun_lower} ships with a printed guide and a spare parts card. Register it online for the extended warranty.</p>
<table class="specs"><caption>Specifications</caption><tbody>{specs}</tbody></table>
</section>
</article>
{reviews_section}
<section class="related" aria-labelledby="rel-h"><div class="section-head"><h2 id="rel-h">More in {cat_name}</h2><a href="/search.html?q={cat}">See all</a></div><div class="card-grid">{related}</div></section>""").format(
        cat=p["category"], cat_name=escape(p["category_name"]), title=escape(p["title"]), id=p["id"],
        image=p["image"], thumbs=thumbs, brand=escape(p["brand"]), rating=p["rating"], stars=stars_html(p["rating"]),
        reviews=p["reviews"], price=p["price"], swatches=swatches, options=options, perks=perks,
        blurb=escape(p["blurb"]), noun_lower=escape(p["noun"].lower()), specs=specs,
        reviews_section="{reviews_section}", related=related)
    return body.replace("{reviews_section}", reviews_html(p))


def cart_recommendations(products: list[dict]) -> list[dict]:
    return sorted(products, key=lambda p: (-p["reviews"], p["id"]))[:CART_RECOMMENDATIONS]


def cart_body(products: list[dict]) -> str:
    recs = "".join(card_html(p, "recommended") for p in cart_recommendations(products))
    return minify("""<nav class="breadcrumb" aria-label="Breadcrumb"><ol><li><a href="/">Home</a></li><li aria-current="page">Cart</li></ol></nav>
<section class="cart" aria-labelledby="cart-h">
<h1 id="cart-h">Your cart</h1>
<ul class="cart-items" id="cart-items"></ul>
<p class="cart-empty" data-testid="cart-empty" hidden>Your cart is empty.</p>
<div class="cart-summary"><dl><div><dt>Subtotal</dt><dd data-testid="cart-subtotal">$0.00</dd></div><div><dt>Shipping</dt><dd>Free</dd></div><div><dt>Total</dt><dd data-testid="cart-total">$0.00</dd></div></dl><button type="button" class="button button-primary" data-testid="checkout" disabled>Checkout</button></div>
</section>
<section class="related" aria-labelledby="rec-h"><div class="section-head"><h2 id="rec-h">Customers also bought</h2></div><div class="card-grid">{recs}</div></section>""").format(recs=recs)


# The cart row template lives in app.js only (it is never rendered statically);
# a copy of it lives here so the manifest can count its nodes.
CART_ITEM_TEMPLATE = """<li class="cart-item" data-testid="cart-item" data-product-id="{id}">
<img src="{image}" alt="" width="320" height="240">
<div class="cart-item-body"><h3 class="cart-item-title" data-testid="cart-item-title">{title}</h3><p class="card-brand">{brand}</p><p class="cart-item-unit">{price} each</p></div>
<div class="cart-item-qty"><button type="button" data-action="dec" aria-label="Decrease">&minus;</button><span data-testid="cart-item-qty">{qty}</span><button type="button" data-action="inc" aria-label="Increase">+</button></div>
<span class="cart-item-line" data-testid="cart-item-line">{line}</span>
<button type="button" class="link" data-action="remove">Remove</button>
</li>"""


# ---------------------------------------------------------------------------
# Measuring: DOM counts and byte accounting
# ---------------------------------------------------------------------------

class DomCounter(HTMLParser):
    """Counts what a browser would build from this markup: elements and text nodes.

    The build emits no whitespace between tags, so every data run is a real
    text node. <script>/<style> bodies count as one text node each, as in a DOM.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements = 0
        self.text_nodes = 0
        self.images = set()
        self._buf = ""

    def _flush(self) -> None:
        if self._buf:
            self.text_nodes += 1
            self._buf = ""

    def handle_starttag(self, tag, attrs):
        self._flush()
        self.elements += 1
        if tag == "img":
            for k, v in attrs:
                if k == "src" and v:
                    self.images.add(v)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        self._flush()

    def handle_data(self, data):
        if data:
            self._buf += data

    def close(self):
        super().close()
        self._flush()


def count_dom(html: str) -> tuple[int, int, set[str]]:
    c = DomCounter()
    c.feed(html)
    c.close()
    return c.elements, c.elements + c.text_nodes, c.images


ASSET_CLASS = {".html": "document", ".css": "css", ".js": "js", ".json": "data", ".png": "image", ".svg": "image"}


def asset_class(path: str) -> str:
    return ASSET_CLASS.get(Path(path).suffix, "other")


class Site:
    """Collects written files so pages can be measured after the fact."""

    def __init__(self, out: Path) -> None:
        self.out = out
        self.sizes: dict[str, int] = {}

    def write(self, url_path: str, data: bytes) -> None:
        target = self.out / url_path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.sizes[url_path] = len(data)

    def page_measure(self, url_path: str, html: str, with_catalog: bool) -> dict:
        elements, nodes, images = count_dom(html)
        refs = ["/favicon.svg", "/styles.css", "/app.js"] + (["/catalog.js"] if with_catalog else []) + sorted(images)
        by_class: dict[str, int] = {"document": len(html.encode("utf-8"))}
        for ref in refs:
            by_class[asset_class(ref)] = by_class.get(asset_class(ref), 0) + self.sizes[ref]
        return {
            "path": url_path,
            "bytes_by_class": by_class,
            "bytes_total": sum(by_class.values()),
            "request_count": 1 + len(refs),
            "image_request_count": len(images),
            "dom_element_count": elements,
            "dom_node_count": nodes,
            "_urls": [url_path] + refs,
        }


def check_range(name: str, value: float, lo: float, hi: float, problems: list[str]) -> None:
    if not lo <= value <= hi:
        problems.append("{} = {} is outside [{}, {}]".format(name, value, lo, hi))


# ---------------------------------------------------------------------------
# Task products
# ---------------------------------------------------------------------------

def choose_query(products: list[dict], p: dict) -> str | None:
    """The shortest query that ranks p first with a strictly higher score than any other product."""
    candidates = [
        p["noun"],
        "{} {}".format(p["adjective"], p["noun"]),
        "{} {}".format(p["brand"], p["noun"]),
        "{} {} {}".format(p["brand"], p["adjective"], p["noun"]),
        p["title"],
    ]
    for q in candidates:
        matches, _ = rank_products(products, q.lower())
        if matches and matches[0][0]["id"] == p["id"] and (len(matches) == 1 or matches[1][1] < matches[0][1]):
            return q.lower()
    return None


def choose_task_products(products: list[dict], rng: random.Random) -> list[dict]:
    """20 products spread over the categories, each with a query that ranks it first."""
    by_cat: dict[str, list[dict]] = {}
    for p in products:
        by_cat.setdefault(p["category"], []).append(p)
    chosen: list[dict] = []
    cats = [slug for slug, _, _ in CATEGORIES]
    i = 0
    while len(chosen) < TASK_PRODUCTS:
        pool = [p for p in by_cat[cats[i % len(cats)]] if p not in chosen]
        rng.shuffle(pool)
        for p in pool:
            q = choose_query(products, p)
            if q:
                chosen.append({"product_id": p["id"], "query": q, "expected_title": p["title"]})
                break
        i += 1
    return chosen


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build(out: Path, seed: int = DEFAULT_SEED, quiet: bool = False) -> dict:
    def log(msg: str) -> None:
        if not quiet:
            print(msg)

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    site = Site(out)
    rng = random.Random(seed)

    products = build_catalog(random.Random(rng.getrandbits(32)))
    catalog = public_catalog(products)
    catalog_json = json.dumps({"products": catalog}, separators=(",", ":"), sort_keys=True).encode("utf-8")
    site.write("/catalog.json", catalog_json)
    site.write("/catalog.js", b"window.FLEETKIT_CATALOG=" + catalog_json + b";\n")
    log("catalog: {} products, {} bytes".format(len(catalog), len(catalog_json)))

    # Static assets. app.js gets the card template and the cart-item template injected.
    app_js = (SRC / "app.js").read_text(encoding="utf-8")
    app_js = (app_js.replace("__CARD_TEMPLATE__", json.dumps(minify(CARD_TEMPLATE)))
              .replace("__CART_ITEM_TEMPLATE__", json.dumps(minify(CART_ITEM_TEMPLATE)))
              .replace("__ADD_TO_CART_DELAY_MS__", str(ADD_TO_CART_DELAY_MS)))
    site.write("/app.js", app_js.encode("utf-8"))
    site.write("/styles.css", (SRC / "styles.css").read_bytes())
    site.write("/favicon.svg", (SRC / "favicon.svg").read_bytes())

    # Images.
    img_rng = random.Random(rng.getrandbits(32))
    for p in products:
        w, h = MAIN_IMAGE
        site.write(p["image"], make_image(p["_image_seed"], w, h, p["colors"][0], p["colors"][1], SHAPES[p["category"]]))
    for g in range(GALLERY_POOL):
        w, h = GALLERY_IMAGE
        bg, fg = img_rng.sample(COLORS, 2)
        site.write("/img/g/{:02d}.png".format(g), make_image(img_rng.getrandbits(32), w, h, bg, fg, img_rng.choice(list(SHAPES.values()))))
    for slug, _, _ in CATEGORIES[:CATEGORY_TILES]:
        w, h = TILE_IMAGE
        bg, fg = img_rng.sample(COLORS, 2)
        site.write("/img/c/{}.png".format(slug), make_image(img_rng.getrandbits(32), w, h, bg, fg, SHAPES[slug]))
    for i in range(COLLECTION_TILES):
        w, h = TILE_IMAGE
        bg, fg = img_rng.sample(COLORS, 2)
        site.write("/img/s/{:02d}.png".format(i), make_image(img_rng.getrandbits(32), w, h, bg, fg, img_rng.choice(list(SHAPES.values()))))
    w, h = HERO_IMAGE
    site.write("/img/hero.png", make_image(img_rng.getrandbits(32), w, h, "#264653", "#f2c14e", "triangle"))
    log("images: {} files".format(sum(1 for k in site.sizes if k.endswith(".png"))))

    # Pages.
    home = page_html("Fleetkit Outfitters", home_body(products), "home")
    site.write("/index.html", home.encode("utf-8"))
    search_shell = page_html("Search - Fleetkit Outfitters", search_body(products), "search", with_catalog=True)
    site.write("/search.html", search_shell.encode("utf-8"))
    cart = page_html("Cart - Fleetkit Outfitters", cart_body(products), "cart", with_catalog=True)
    site.write("/cart.html", cart.encode("utf-8"))
    product_pages: dict[str, str] = {}
    for p in products:
        html = page_html("{} - Fleetkit Outfitters".format(p["title"]), product_body(products, p), "product")
        product_pages[p["id"]] = html
        site.write("/p/{}.html".format(p["id"]), html.encode("utf-8"))
    log("pages: home, search, cart and {} product pages".format(len(products)))

    # Task products and their queries.
    task_products = choose_task_products(products, random.Random(rng.getrandbits(32)))
    site.write("/task-products.json", (json.dumps(task_products, indent=2) + "\n").encode("utf-8"))

    # Measure. The search page is rendered here exactly as app.js renders it
    # (same template, same ranking) for the first task query; every query
    # renders CARDS_PER_PAGE cards, so the counts hold for any query.
    def rendered_search(query: str) -> str:
        cards = search_page_cards(products, query)
        n_matches = sum(1 for _, m in cards)
        html_cards = "".join(card_html(p, "result" if m else "related", "" if m else " card-related") for p, m in cards)
        text = "{} result{} for “{}”".format(n_matches, "" if n_matches == 1 else "s", query)
        return page_html("Search - Fleetkit Outfitters", search_body(products, html_cards, text), "search", query, with_catalog=True)

    def rendered_cart(p: dict) -> str:
        row = render_template(minify(CART_ITEM_TEMPLATE), {"id": p["id"], "image": p["image"], "title": p["title"],
                                                            "brand": p["brand"], "price": p["price"], "qty": 1, "line": p["price"]})
        return cart.replace('<ul class="cart-items" id="cart-items"></ul>', '<ul class="cart-items" id="cart-items">' + row + "</ul>")

    by_id = {p["id"]: p for p in products}
    problems: list[str] = []
    home_m = site.page_measure("/", home, with_catalog=False)
    search_m = site.page_measure("/search.html?q=" + task_products[0]["query"].replace(" ", "+"),
                                 rendered_search(task_products[0]["query"]), with_catalog=True)
    product_ms = [site.page_measure("/p/{}.html".format(pid), html, with_catalog=False) for pid, html in product_pages.items()]
    # Measure the cart with a task product that is not also a recommendation, so
    # its row image is a fresh request (the common case: 13 images, not 12).
    rec_ids = {p["id"] for p in cart_recommendations(products)}
    cart_product = next(by_id[t["product_id"]] for t in task_products if t["product_id"] not in rec_ids)
    cart_m = site.page_measure("/cart.html", rendered_cart(cart_product), with_catalog=True)

    def summarize(ms: list[dict], path: str) -> dict:
        keys = ["bytes_total", "request_count", "image_request_count", "dom_element_count", "dom_node_count"]
        s = {"path": path, "bytes_by_class": {}}
        for cls in sorted({c for m in ms for c in m["bytes_by_class"]}):
            s["bytes_by_class"][cls] = round(sum(m["bytes_by_class"].get(cls, 0) for m in ms) / len(ms))
        for k in keys:
            vals = [m[k] for m in ms]
            s[k] = round(sum(vals) / len(vals))
            s[k + "_min"] = min(vals)
            s[k + "_max"] = max(vals)
        s["sample_size"] = len(ms)
        return s

    def public(m: dict) -> dict:
        return {k: v for k, v in m.items() if not k.startswith("_")}

    pages = {"home": public(home_m), "search": public(search_m), "product": summarize(product_ms, "/p/<id>.html"),
             "cart": public(cart_m)}
    pages["search"]["path"] = "/search.html?q=<query>"
    pages["search"]["note"] = "Rendered client-side; every query renders {} cards, so the counts hold for any query.".format(CARDS_PER_PAGE)
    pages["cart"]["note"] = "Measured with one item in the cart, as verify_cart sees it; one image fewer when the item is also one of the {} recommendations.".format(CART_RECOMMENDATIONS)
    pages["product"]["note"] = "Mean over all {} product pages; _min/_max give the spread.".format(len(products))

    for name, m in (("home", home_m), ("search", search_m), ("cart", cart_m)):
        check_range("{}.request_count".format(name), m["request_count"], 1, 200, problems)
    for name, m in (("search", search_m),):
        check_range("search.bytes_total", m["bytes_total"], *PAGE_BYTES_RANGE, problems)
        check_range("search.dom_element_count", m["dom_element_count"], *DOM_RANGE, problems)
        check_range("search.dom_node_count", m["dom_node_count"], *DOM_RANGE, problems)
        check_range("search.image_request_count", m["image_request_count"], *IMAGE_REQUESTS_RANGE, problems)
    for m in product_ms:
        check_range("product.bytes_total", m["bytes_total"], *PAGE_BYTES_RANGE, problems)
        check_range("product.dom_element_count", m["dom_element_count"], *DOM_RANGE, problems)
        check_range("product.dom_node_count", m["dom_node_count"], *DOM_RANGE, problems)
        check_range("product.image_request_count", m["image_request_count"], *IMAGE_REQUESTS_RANGE, problems)
    check_range("home.image_request_count", home_m["image_request_count"], *IMAGE_REQUESTS_RANGE, problems)
    check_range("home.bytes_total", home_m["bytes_total"], *PAGE_BYTES_RANGE, problems)

    # Per task product: the exact task path, counting every reference (naive) and unique URLs.
    per_task = {}
    product_m_by_id = {m["path"].split("/")[-1][:-5]: m for m in product_ms}
    for t in task_products:
        p = by_id[t["product_id"]]
        path_pages = [home_m, site.page_measure("/search.html?q=" + t["query"].replace(" ", "+"), rendered_search(t["query"]), True),
                      product_m_by_id[p["id"]], site.page_measure("/cart.html", rendered_cart(p), True)]
        naive_requests = sum(m["request_count"] for m in path_pages)
        naive_bytes = sum(m["bytes_total"] for m in path_pages)
        unique: dict[str, int] = {}
        for m in path_pages:
            doc_url = m["_urls"][0]
            unique[doc_url] = m["bytes_by_class"]["document"]
            for u in m["_urls"][1:]:
                unique[u] = site.sizes[u]
        per_task[p["id"]] = {
            "query": t["query"],
            "expected_bytes": sum(unique.values()),
            "expected_request_count": naive_requests,
            "unique_request_count": len(unique),
            "naive_bytes": naive_bytes,
        }
        assert search_page_cards(products, t["query"])[0][0]["id"] == p["id"]

    expected_bytes = round(sum(v["expected_bytes"] for v in per_task.values()) / len(per_task))
    expected_requests = round(sum(v["expected_request_count"] for v in per_task.values()) / len(per_task))
    unique_requests = round(sum(v["unique_request_count"] for v in per_task.values()) / len(per_task))

    manifest = {
        "schema": "fleetkit-fixture-manifest/1",
        "generated_by": "fixture/build.py",
        "seed": seed,
        "catalog_size": len(products),
        "task_product_count": len(task_products),
        "add_to_cart_delay_ms": ADD_TO_CART_DELAY_MS,
        "cards_per_page": CARDS_PER_PAGE,
        "expected_bytes": expected_bytes,
        "expected_request_count": expected_requests,
        "tolerance": {"bytes_pct": 15, "requests_pct": 20},
        "task": {
            "pages": ["home", "search", "product", "cart"],
            "expected_bytes": expected_bytes,
            "expected_request_count": expected_requests,
            "unique_request_count": unique_requests,
            "bytes_definition": "Sum of the bytes of every distinct URL the four pages reference (each shared asset counted once, as a caching browser fetches it). Chromium's Network.loadingFinished.encodedDataLength also counts response headers, and a gzip-enabled server shrinks the text assets; both sit inside the tolerance.",
            "requests_definition": "Every reference on every page, so a request served from disk cache (which still emits Network.requestWillBeSent) is counted; unique_request_count is the floor if every repeat is a memory-cache hit.",
            "per_product": per_task,
        },
        "pages": pages,
        "selectors": {
            "home.search_input": "input[name=q]",
            "search.result": "[data-testid=result][data-product-id]",
            "product.title": "h1[data-testid=product-title]",
            "product.add_to_cart": "button[data-testid=add-to-cart]",
            "product.added": "[data-testid=added]",
            "cart_badge": "[data-testid=cart-count]",
            "cart.item": "[data-testid=cart-item][data-product-id]",
            "cart.item_title": "[data-testid=cart-item-title]",
        },
        "bytes_total": sum(site.sizes.values()),
        "file_count": len(site.sizes),
    }
    site.write("/manifest.json", (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    if problems:
        for p in problems:
            print("self-check failed: " + p, file=sys.stderr)
        raise SystemExit(2)

    log("search page: {} bytes, {} requests ({} images), {} elements / {} nodes".format(
        search_m["bytes_total"], search_m["request_count"], search_m["image_request_count"],
        search_m["dom_element_count"], search_m["dom_node_count"]))
    log("product page (mean): {} bytes, {} requests ({} images), {} elements / {} nodes".format(
        pages["product"]["bytes_total"], pages["product"]["request_count"], pages["product"]["image_request_count"],
        pages["product"]["dom_element_count"], pages["product"]["dom_node_count"]))
    log("task: expected {} bytes and {} requests (unique URLs: {})".format(expected_bytes, expected_requests, unique_requests))
    log("dist: {} files, {:.1f} MB".format(manifest["file_count"], manifest["bytes_total"] / 1e6))
    return manifest


def tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(root)).encode())
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=HERE / "dist", help="output directory (default: fixture/dist)")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--digest", action="store_true", help="print a sha256 over the whole output tree")
    args = ap.parse_args(argv)
    build(args.out, args.seed, args.quiet)
    if args.digest:
        print("digest: " + tree_digest(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
