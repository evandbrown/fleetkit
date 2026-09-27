/* Fleetkit Outfitters: search, rendering and cart. Vanilla JavaScript, no dependencies.
 *
 * build.py injects three placeholders: the card template (shared with the
 * static pages), the cart-row template, and the simulated cart latency.
 * The ranking in rankProducts mirrors build.py's rank_products; the build
 * picks task queries with the Python version, so keep them identical.
 */
(function () {
  'use strict';

  var CARD_TEMPLATE = __CARD_TEMPLATE__;
  var CART_ITEM_TEMPLATE = __CART_ITEM_TEMPLATE__;
  var ADD_TO_CART_DELAY_MS = __ADD_TO_CART_DELAY_MS__;
  var CARDS_PER_PAGE = 24;
  var CART_KEY = 'fleetkit.cart';

  // ---- helpers -----------------------------------------------------------

  function esc(value) {
    return String(value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#x27;');
  }

  function render(template, fields) {
    return template.replace(/\{(\w+)\}/g, function (_, key) { return esc(fields[key]); });
  }

  function tokens(text) {
    return String(text).toLowerCase().split(/[^a-z0-9]+/).filter(function (t) { return t; });
  }

  function catalog() {
    return (window.FLEETKIT_CATALOG && window.FLEETKIT_CATALOG.products) || [];
  }

  function productById(id) {
    var list = catalog();
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
    return null;
  }

  function cardFields(p, testid, extraClass) {
    var f = {
      id: p.id, title: p.title, brand: p.brand, image: p.image, category_name: p.category_name,
      price: p.price, rating: p.rating, reviews: p.reviews, blurb: p.blurb, testid: testid,
      extra_class: extraClass || '', tag1: p.tags[0], tag2: p.tags[1]
    };
    var full = Math.round(p.rating);
    for (var i = 0; i < 5; i++) f['s' + (i + 1)] = i < full ? 'on' : 'off';
    for (i = 0; i < 4; i++) f['c' + (i + 1)] = p.colors[i];
    for (i = 0; i < 3; i++) f['o' + (i + 1)] = p.options[i];
    for (i = 0; i < 4; i++) { f['k' + (i + 1)] = p.specs[i][0]; f['v' + (i + 1)] = p.specs[i][1]; }
    for (i = 0; i < 3; i++) f['perk' + (i + 1)] = p.perks[i];
    return f;
  }

  function cardHtml(p, testid, extraClass) {
    return render(CARD_TEMPLATE, cardFields(p, testid, extraClass));
  }

  // ---- search ------------------------------------------------------------

  function indexProduct(p) {
    var idx = { title: {}, brand: {}, category: {}, tags: {} };
    tokens(p.title).forEach(function (t) { idx.title[t] = true; });
    tokens(p.brand).forEach(function (t) { idx.brand[t] = true; });
    tokens(p.category).concat(tokens(p.category_name)).forEach(function (t) { idx.category[t] = true; });
    p.tags.forEach(function (tag) { tokens(tag).forEach(function (t) { idx.tags[t] = true; }); });
    return idx;
  }

  function scoreProduct(idx, qtokens) {
    var total = 0;
    for (var i = 0; i < qtokens.length; i++) {
      var t = qtokens[i], s = 0;
      if (idx.title[t]) s += 5;
      if (idx.brand[t]) s += 3;
      if (idx.category[t]) s += 2;
      if (idx.tags[t]) s += 1;
      if (s === 0) return 0; // every query token must match somewhere
      total += s;
    }
    return total;
  }

  function rankProducts(products, query) {
    var qtokens = tokens(query);
    var matches = [];
    if (qtokens.length) {
      products.forEach(function (p) {
        var s = scoreProduct(p._idx || (p._idx = indexProduct(p)), qtokens);
        if (s > 0) matches.push({ product: p, score: s });
      });
      matches.sort(function (a, b) {
        if (b.score !== a.score) return b.score - a.score;
        return a.product.id < b.product.id ? -1 : a.product.id > b.product.id ? 1 : 0;
      });
    }
    var matched = {};
    matches.forEach(function (m) { matched[m.product.id] = true; });
    var sum = 0;
    for (var ch of String(query)) sum += ch.codePointAt(0);
    var offset = sum % products.length;
    var filler = [];
    for (var i = 0; i < products.length; i++) {
      var p = products[(offset + i) % products.length];
      if (!matched[p.id]) filler.push(p);
    }
    return { matches: matches, filler: filler };
  }

  function renderSearch() {
    var query = new URLSearchParams(window.location.search).get('q') || '';
    var input = document.querySelector('input[name=q]');
    if (input) input.value = query;
    var ranked = rankProducts(catalog(), query);
    var cards = [];
    ranked.matches.slice(0, CARDS_PER_PAGE).forEach(function (m) { cards.push({ p: m.product, match: true }); });
    for (var i = 0; cards.length < CARDS_PER_PAGE && i < ranked.filler.length; i++) cards.push({ p: ranked.filler[i], match: false });
    var html = cards.map(function (c) {
      return c.match ? cardHtml(c.p, 'result', '') : cardHtml(c.p, 'related', ' card-related');
    }).join('');
    var grid = document.getElementById('results');
    grid.innerHTML = html;
    var nMatches = Math.min(ranked.matches.length, CARDS_PER_PAGE);
    var countEl = document.querySelector('[data-testid=result-count]');
    if (countEl) countEl.textContent = nMatches + (nMatches === 1 ? ' result for “' : ' results for “') + query + '”';
    document.title = (query ? query + ' - ' : '') + 'Search - Fleetkit Outfitters';
  }

  // ---- cart --------------------------------------------------------------

  var Cart = {
    read: function () {
      try {
        var raw = window.localStorage.getItem(CART_KEY);
        var data = raw ? JSON.parse(raw) : null;
        if (data && Array.isArray(data.items)) return data;
      } catch (e) { /* fall through to an empty cart */ }
      return { items: [] };
    },
    write: function (data) {
      try { window.localStorage.setItem(CART_KEY, JSON.stringify(data)); } catch (e) { /* storage unavailable */ }
    },
    add: function (id, qty) {
      var data = this.read();
      var found = null;
      data.items.forEach(function (it) { if (it.product_id === id) found = it; });
      if (found) found.qty += qty; else data.items.push({ product_id: id, qty: qty });
      this.write(data);
      return data;
    },
    setQty: function (id, qty) {
      var data = this.read();
      data.items = data.items.map(function (it) { return it.product_id === id ? { product_id: id, qty: qty } : it; })
        .filter(function (it) { return it.qty > 0; });
      this.write(data);
      return data;
    },
    count: function () {
      return this.read().items.reduce(function (n, it) { return n + it.qty; }, 0);
    }
  };

  function updateBadge() {
    var badge = document.querySelector('[data-testid=cart-count]');
    if (badge) badge.textContent = String(Cart.count());
  }

  function priceCents(price) {
    return Math.round(parseFloat(String(price).replace(/[^0-9.]/g, '')) * 100);
  }

  function money(cents) {
    var s = (cents / 100).toFixed(2);
    return '$' + s.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  function renderCart() {
    var list = document.getElementById('cart-items');
    var empty = document.querySelector('[data-testid=cart-empty]');
    var data = Cart.read();
    var subtotal = 0;
    var rows = [];
    data.items.forEach(function (it) {
      var p = productById(it.product_id);
      if (!p) return;
      var line = priceCents(p.price) * it.qty;
      subtotal += line;
      rows.push(render(CART_ITEM_TEMPLATE, {
        id: p.id, image: p.image, title: p.title, brand: p.brand, price: p.price, qty: it.qty, line: money(line)
      }));
    });
    list.innerHTML = rows.join('');
    if (empty) empty.hidden = rows.length > 0;
    var sub = document.querySelector('[data-testid=cart-subtotal]');
    var total = document.querySelector('[data-testid=cart-total]');
    if (sub) sub.textContent = money(subtotal);
    if (total) total.textContent = money(subtotal);
    var checkout = document.querySelector('[data-testid=checkout]');
    if (checkout) checkout.disabled = rows.length === 0;
    updateBadge();
  }

  function bindCart() {
    document.getElementById('cart-items').addEventListener('click', function (ev) {
      var btn = ev.target.closest('button[data-action]');
      if (!btn) return;
      var row = btn.closest('[data-testid=cart-item]');
      var id = row.getAttribute('data-product-id');
      var qtyEl = row.querySelector('[data-testid=cart-item-qty]');
      var qty = parseInt(qtyEl.textContent, 10) || 1;
      if (btn.dataset.action === 'inc') Cart.setQty(id, qty + 1);
      else if (btn.dataset.action === 'dec') Cart.setQty(id, qty - 1);
      else Cart.setQty(id, 0);
      renderCart();
    });
  }

  // ---- product page --------------------------------------------------------

  function bindProduct() {
    var btn = document.querySelector('button[data-testid=add-to-cart]');
    if (!btn) return;
    btn.addEventListener('click', function () {
      var id = btn.getAttribute('data-product-id');
      var qtyInput = document.getElementById('qty');
      var qty = Math.max(1, parseInt(qtyInput && qtyInput.value, 10) || 1);
      btn.disabled = true;
      btn.textContent = 'Adding…';
      // A real store would wait for its cart API here; the fixture waits a fixed,
      // documented delay so a harness must observe the DOM rather than assume.
      window.setTimeout(function () {
        Cart.add(id, qty);
        updateBadge();
        var note = document.querySelector('[data-testid=added]');
        if (!note) {
          note = document.createElement('p');
          note.className = 'added';
          note.setAttribute('data-testid', 'added');
          note.setAttribute('role', 'status');
          btn.parentNode.insertBefore(note, btn.nextSibling);
        }
        note.textContent = 'Added to cart';
        btn.textContent = 'Add to cart';
        btn.disabled = false;
      }, ADD_TO_CART_DELAY_MS);
    });

    var main = document.getElementById('gallery-main');
    document.querySelectorAll('.gallery-thumbs .thumb').forEach(function (thumb) {
      thumb.addEventListener('click', function () { if (main) main.src = thumb.getAttribute('data-src'); });
    });
    document.querySelectorAll('.chips .chip').forEach(function (chip) {
      chip.addEventListener('click', function () {
        document.querySelectorAll('.chips .chip').forEach(function (c) { c.classList.remove('selected'); });
        chip.classList.add('selected');
      });
    });
  }

  // ---- boot --------------------------------------------------------------

  var page = document.body.getAttribute('data-page');
  updateBadge();
  if (page === 'search') renderSearch();
  else if (page === 'cart') { renderCart(); bindCart(); }
  else if (page === 'product') bindProduct();

  window.Fleetkit = { rankProducts: rankProducts, Cart: Cart, cardHtml: cardHtml };
})();
