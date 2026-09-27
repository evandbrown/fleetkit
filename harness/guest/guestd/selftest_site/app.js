// Selftest shop: the smallest site that satisfies the task's selectors (design section 4).
// State lives in localStorage; every page updates the cart badge on load.
(function () {
  'use strict';
  var KEY = 'fleetkit-selftest-cart';

  function readCart() {
    try { return JSON.parse(localStorage.getItem(KEY) || '[]'); } catch (e) { return []; }
  }
  function writeCart(items) { localStorage.setItem(KEY, JSON.stringify(items)); }
  function updateBadge() {
    var badge = document.querySelector('[data-testid=cart-count]');
    if (badge) badge.textContent = String(readCart().length);
  }
  function loadProducts() {
    return fetch('/products.json').then(function (r) { return r.json(); });
  }

  function renderSearch() {
    var list = document.querySelector('[data-testid=results]');
    if (!list) return;
    var q = (new URLSearchParams(location.search).get('q') || '').toLowerCase().trim();
    loadProducts().then(function (products) {
      var hits = products.filter(function (p) {
        return !q || p.title.toLowerCase().indexOf(q) >= 0 || p.query === q;
      });
      list.textContent = '';
      hits.forEach(function (p) {
        var li = document.createElement('li');
        li.setAttribute('data-testid', 'result');
        li.setAttribute('data-product-id', p.id);
        var a = document.createElement('a');
        a.href = '/p/' + p.id + '.html';
        a.textContent = p.title;
        li.appendChild(a);
        list.appendChild(li);
      });
      var status = document.querySelector('[data-testid=result-count]');
      if (status) status.textContent = hits.length + ' result(s) for "' + q + '"';
    });
  }

  function wireProduct() {
    var button = document.querySelector('button[data-testid=add-to-cart]');
    if (!button) return;
    button.addEventListener('click', function () {
      var items = readCart();
      items.push({ id: button.getAttribute('data-product-id'), title: button.getAttribute('data-title') });
      writeCart(items);
      // The confirmation appears on a later turn of the event loop, so the daemon's
      // MutationObserver wait is exercised rather than satisfied at once.
      setTimeout(function () {
        var added = document.querySelector('[data-testid=added]');
        if (added) added.hidden = false;
        updateBadge();
      }, 40);
    });
  }

  function renderCart() {
    var list = document.querySelector('[data-testid=cart-items]');
    if (!list) return;
    list.textContent = '';
    readCart().forEach(function (item) {
      var li = document.createElement('li');
      li.setAttribute('data-testid', 'cart-item');
      li.setAttribute('data-product-id', item.id);
      var span = document.createElement('span');
      span.setAttribute('data-testid', 'cart-item-title');
      span.textContent = item.title;
      li.appendChild(span);
      list.appendChild(li);
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    updateBadge();
    renderSearch();
    wireProduct();
    renderCart();
  });
})();
