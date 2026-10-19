// Exercise the application's actual legacy rendering functions with hostile
// server data. No browser, server, network, dependencies, or file cleanup.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { test } = require('node:test');
const root = path.join(__dirname, '..');
const hostile = `<img src=x onerror="globalThis.__xss=1"><svg onload='globalThis.__xss=2'>`;
const attribute = `" autofocus onfocus="globalThis.__xss=3" data-test="`;
const apostrophe = `O'Brien');globalThis.__xss=4;//`;

function source(name) { return fs.readFileSync(path.join(root, 'static/js', name), 'utf8').replace(/\r\n/g, '\n'); }
function section(name, startMarker, endMarker) {
  const code = source(name);
  const start = code.indexOf(startMarker);
  const end = code.indexOf(endMarker, start + startMarker.length);
  assert.notEqual(start, -1, `Missing real rendering function: ${startMarker}`);
  assert.notEqual(end, -1, `Missing source boundary: ${endMarker}`);
  return code.slice(start, end);
}

function element(tag = 'div') {
  const node = {
    tagName: tag, children: [], listeners: new Map(), selectorChildren: new Map(),
    style: {}, dataset: {}, className: '', textContent: '', _html: '',
    classList: { add() {}, remove() {}, contains() { return false; } },
    appendChild(child) { this.children.push(child); return child; },
    append(...children) { this.children.push(...children); },
    prepend(child) { this.children.unshift(child); },
    addEventListener(event, callback) { this.listeners.set(event, callback); },
    querySelector(selector) {
      if (!this.selectorChildren.has(selector)) this.selectorChildren.set(selector, element());
      return this.selectorChildren.get(selector);
    },
    remove() {},
    replaceChildren(...children) { this.children = children; },
    scrollIntoView() {}, focus() {}, reportValidity() { return true; },
    set innerHTML(value) { this._html = String(value); this.children = []; },
    get innerHTML() { return this._html; },
  };
  return node;
}

function harness(extra = {}) {
  const context = {
    console, URL, URLSearchParams, encodeURIComponent, Number, String, Set,
    document: {
      createElement: tag => element(tag), createTextNode: value => ({ nodeType: 3, textContent: String(value) }),
      querySelectorAll: () => [], body: element('body'),
    },
    location: { href: 'http://localhost:5000/admin', origin: 'http://localhost:5000' },
    setTimeout() {}, clearTimeout() {}, ...extra,
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(source('dom-safety.js'), context);
  return context;
}

function assertEncoded(html, payload, dom) {
  assert.ok(html.includes(dom.escapeHtml(payload)), 'Real renderer must preserve the value as escaped text');
  assert.ok(!html.includes(payload), 'Hostile source must not be inserted as HTML');
  assert.doesNotMatch(html, /<(?:img|svg|script|iframe)\b/i);
}

test('shared text and URL helpers reject markup/protocol injection without corrupting Turkish text', () => {
  const h = harness();
  const D = h.BrivaDOM;
  assert.equal(D.escapeHtml(`Çınar & "İpek" < M > 'S'`), 'Çınar &amp; &quot;İpek&quot; &lt; M &gt; &#39;S&#39;');
  for (const value of ['javascript:alert(1)', 'data:text/html,<script>x</script>', '//foreign.example/a', 'https://foreign.example/', 'http://user:password@localhost:5000/']) {
    assert.equal(D.sameOriginURL(value), '', value);
  }
  assert.equal(D.sameOriginURL('/static/uploads/shirt.png'), '/static/uploads/shirt.png');
  for (const value of [true, null, '', Infinity, 'NaN', hostile]) assert.equal(D.finiteNumber(value), 0);
  assert.equal(D.finiteNumber('123.45'), 123.45);
  const n = element(); D.text(n, hostile); assert.equal(n.textContent, hostile); assert.equal(n.innerHTML, '');
});

test('owner user table cannot execute HTML or apostrophes from names, company and phone', () => {
  const body = element('tbody');
  let edited;
  const record = { id: 7, username: hostile, role: attribute, company: apostrophe, phone: hostile };
  const h = harness({
    tableBody: body, getFilteredData: () => [record], showingBayiler: false,
    selectedUserIds: new Set(), currentPage: 1, rowsPerPage: 10,
    renderPagination() {}, updateSelectionUI() {},
    openEditUser: user => { edited = user; }, deleteUser() {},
  });
  vm.runInContext(section('admin.js', 'function renderTable() {', '// Sayfalama\n'), h);
  h.renderTable();
  const row = body.children[0];
  assertEncoded(row.innerHTML, hostile, h.BrivaDOM);
  assertEncoded(row.innerHTML, apostrophe, h.BrivaDOM);
  assertEncoded(row.innerHTML, attribute, h.BrivaDOM);
  assert.doesNotMatch(row.innerHTML, /onclick\s*=/i, 'Data must not become inline JavaScript');
  row.querySelector('.js-edit-user').listeners.get('click')();
  assert.equal(edited, record, 'Click callback must receive the original object without HTML serialization');
  assert.equal(h.__xss, undefined);
});

test('owner company names and toast messages remain text', () => {
  const body = element('tbody');
  const h = harness({
    tableBody: body, getFilteredData: () => [{ id: 'own-brand', name: hostile }],
    showingBayiler: true, currentPage: 1, rowsPerPage: 10,
    renderPagination() {}, updateSelectionUI() {},
  });
  vm.runInContext(section('admin.js', 'function renderTable() {', '// Sayfalama\n'), h);
  h.renderTable();
  assertEncoded(body.children[0].innerHTML, hostile, h.BrivaDOM);
  vm.runInContext(section('admin.js', 'function showMessage(', '// Global LogOut'), h);
  h.showMessage(hostile, 'error');
  const toast = h.document.body.children[0];
  assert.equal(toast.innerHTML, '');
  assert.equal(toast.children[1].textContent, hostile);
});

test('manager employee table and rankings encode usernames', () => {
  const body = element('tbody'), rankings = element();
  const h = harness({ tabloBody: body, topEmployeesList: rankings });
  vm.runInContext(section('manager.js', '    function renderTable(users) {', '    async function fetchUsers()'), h);
  h.renderTable([{ id: 8, username: hostile }]);
  assertEncoded(body.children[0].innerHTML, hostile, h.BrivaDOM);
  vm.runInContext(section('manager.js', '    function renderTopEmployeesList(employees)', '    function activateTabFromHash()'), h);
  h.renderTopEmployeesList([{ username: hostile, sales_count: attribute }]);
  assertEncoded(rankings.children[0].innerHTML, hostile, h.BrivaDOM);
  assert.ok(rankings.children[0].innerHTML.includes('0 satış'));
});

test('manager sales history encodes customer, item, size, currency and quoted IDs', () => {
  const body = element('tbody');
  const h = harness({ allSalesBody: body });
  vm.runInContext(section('manager.js', '    function renderAllSalesTable(salesGroups)', '    function renderPagination('), h);
  h.renderAllSalesTable([{
    customer_name: hostile, sold_by: apostrophe, sale_date: hostile, sale_group_id: attribute,
    totals: { [hostile]: 15 }, items: [{ product_name: hostile, product_code: apostrophe,
      color_name: hostile, quantity: 1, sizes_breakdown: [{ quantity: 1, value: hostile }],
      price_at_sale: 15, total_price: 15, currency: hostile }],
  }]);
  assertEncoded(body.children[0].innerHTML, hostile, h.BrivaDOM);
  assertEncoded(body.children[0].innerHTML, attribute, h.BrivaDOM);
  assertEncoded(body.children[1].innerHTML, hostile, h.BrivaDOM);
  assertEncoded(body.children[1].innerHTML, apostrophe, h.BrivaDOM);
});

test('customer debt list encodes names, phone and unexpected currency keys', () => {
  const body = element('tbody');
  const h = harness({ tableBody: body });
  vm.runInContext(section('customer_debts.js', '    function formatDebt(debtObject)', '    /**\n     * Arama'), h);
  vm.runInContext(section('customer_debts.js', '    function renderTable(customers)', '    /**\n     * Borç'), h);
  h.renderTable([{ id: attribute, name: hostile, phone: apostrophe, debt: { [hostile]: 15 } }]);
  assertEncoded(body.children[0].innerHTML, hostile, h.BrivaDOM);
  assertEncoded(body.children[0].innerHTML, attribute, h.BrivaDOM);
  assertEncoded(body.children[0].innerHTML, apostrophe, h.BrivaDOM);
});

test('customer account history safely handles notes and PDF path segments', () => {
  const body = element('tbody');
  const h = harness({ historyTableBody: body });
  vm.runInContext(section('customer_debts.js', '    function renderHistoryTable(history)', '    /**\n     * Ekranda'), h);
  const row = { type: 'CREDIT', date: hostile, user: hostile, amount: 15, currency: hostile, note: hostile };
  h.renderHistoryTable([row]);
  assertEncoded(body.innerHTML, hostile, h.BrivaDOM);
  h.renderHistoryTable([{ ...row, note: '', sale_id: attribute }]);
  assert.ok(body.innerHTML.includes('/generate_transaction_pdf/' + encodeURIComponent(attribute)));
  assert.ok(body.innerHTML.includes('rel="noopener noreferrer"'));
  assert.doesNotMatch(body.innerHTML, /<img\b/i);
});

test('definition and standalone color/customer/category renderers encode stored fields', () => {
  const body = element('tbody');
  const h = harness({ tableBody: body, tbody: body, categoriesTbody: body,
    fmtSizes: values => values.join(' / ') });
  const functions = [
    ['definitions.js', '    function addColorToTable(color)', '    async function fetchAndDisplayColors()', 'addColorToTable', { code: attribute, name: hostile }],
    ['definitions.js', '    function addCustomerRow(customer)', '    // Müşterileri Sunucudan', 'addCustomerRow', { id: attribute, name: hostile, phone: apostrophe }],
    ['addcustomer.js', '  function addCustomerRow(customer)', '  async function loadCustomers()', 'addCustomerRow', { id: attribute, name: hostile, phone: apostrophe }],
    ['addcolor.js', '    function addColorToTable(color)', '    // --- SAYFA YÜKLENDİĞİNDE', 'addColorToTable', { code: attribute, name: hostile }],
    ['addcategory.js', 'function addCategoryRow(cat)', '// === KATEGORİ EKLE', 'addCategoryRow', { id: 1, name: hostile, sizes: [hostile] }],
  ];
  for (const [file, start, end, fn, payload] of functions) {
    body.innerHTML = '';
    vm.runInContext(section(file, start, end), h);
    h[fn](payload);
    assertEncoded(body.children[0].innerHTML, hostile, h.BrivaDOM);
  }
});

test('AI and user messages never pass raw model output or input to HTML/Markdown', () => {
  const messages = element();
  const h = harness({ messagesArea: messages, scrollToBottom() {},
    marked: { parse() { throw new Error('Untrusted Markdown parser must not be called'); } } });
  vm.runInContext(section('ai_chat.js', '    function addMessage(text, sender)', '    // Yükleniyor Göstergesi'), h);
  for (const sender of ['ai', 'user']) {
    h.addMessage(hostile + '\n' + apostrophe, sender);
    const content = messages.children.at(-1).children[0];
    assert.equal(content.textContent, hostile + '\n' + apostrophe);
    assert.equal(content.innerHTML, '');
    assert.equal(content.style.whiteSpace, 'pre-wrap');
  }
});

test('definition category chips and shelf rows encode labels and attribute values', async () => {
  const body = element('tbody');
  const h = harness({ categoriesTbody: body, shelvesTableBody: body,
    fetch: async () => ({ ok: true, json: async () => ({ shelves: [{ id: attribute, code: hostile }] }) }) });
  const category = section('definitions.js', '    const addCategoryRow = (cat) => {', '    async function loadCategories()')
    .replace('const addCategoryRow =', 'globalThis.addCategoryRow =');
  vm.runInContext(category, h);
  h.addCategoryRow({ id: attribute, name: hostile, sizes: [hostile, apostrophe] });
  assertEncoded(body.children[0].innerHTML, hostile, h.BrivaDOM);
  assertEncoded(body.children[0].innerHTML, attribute, h.BrivaDOM);
  body.innerHTML = '';
  vm.runInContext(section('definitions.js', '    async function loadShelves() {', '    shelfForm.addEventListener'), h);
  await h.loadShelves();
  assertEncoded(body.children[0].innerHTML, hostile, h.BrivaDOM);
  assertEncoded(body.children[0].innerHTML, attribute, h.BrivaDOM);
});

test('employee access controls preserve disabled rows and protect owner rows', () => {
  const body = element('tbody');
  const h = harness({ tabloBody: body, isAccountOwner: true });
  vm.runInContext(section('manager.js', '    function renderTable(users) {', '    async function fetchUsers()'), h);
  h.renderTable([{ id: 1, username: 'Sahip', role: 'admin', active: true },
                 { id: 2, username: hostile, role: 'cashier', active: false }]);
  assert.equal(body.children.length, 2);
  assert.doesNotMatch(body.children[0].innerHTML, /access-btn|izin-yonet-btn/);
  assert.ok(body.children[1].innerHTML.includes('Erişim kapalı'));
  assert.ok(body.children[1].innerHTML.includes('Erişimi aç'));
  assertEncoded(body.children[1].innerHTML, hostile, h.BrivaDOM);
});

function accountHarness(mode, fetch) {
  const nodes = new Map();
  const get = selector => {
    if (!nodes.has(selector)) {
      const n = element();
      n.elements = { current_password: { value: 'Synthetic-password-2026' }, code: { value: '123456', focus() {} } };
      n.reset = () => {};
      nodes.set(selector, n);
    }
    return nodes.get(selector);
  };
  const context = harness({ fetch,
    Intl, Date, navigator: {},
    localStorage: new Proxy({}, { get() { throw new Error('Secrets must not use local storage'); } }),
    sessionStorage: new Proxy({}, { get() { throw new Error('Secrets must not use session storage'); } }),
  });
  context.document.querySelector = get;
  context.document.body.dataset.securityMode = mode;
  vm.runInContext(source('account_security.js'), context);
  return { context, get, nodes };
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const accountState = { actor: { id: 7, company: 'BRIVA İstanbul % A', username: hostile },
  mfa_enabled: false, mfa_configured: true, can_manage_mfa: true,
  recovery_codes_remaining: 0, sessions: [{ id: 3, device: hostile, is_current: true,
    created_at: 1700000000, expires_at: 1700003600 }] };

test('account security renders server device labels as text and keeps storage unused', async () => {
  const h = accountHarness('account', async () => ({ ok: true, json: async () => ({ data: accountState }) }));
  await flush();
  const list = h.get('#as-sessions');
  const device = list.children[0].children[0].children[0];
  assert.equal(device.textContent, hostile);
  assert.equal(device.innerHTML, '');
  assert.equal(h.get('#as-account-name').textContent, hostile + ' · BRIVA İstanbul % A');
  assert.equal(h.get('#as-account-name').innerHTML, '');
  assert.equal(h.context.__xss, undefined);
});

test('successful MFA confirmation preserves recovery codes if the following status read fails', async () => {
  const codes = ['SYNTHETIC-CODE-A', 'SYNTHETIC-CODE-B'];
  let reads = 0;
  const h = accountHarness('account', async (path, options) => {
    if (path.endsWith('/mfa/confirm')) {
      assert.equal(options.headers['X-Briva-Actor'], '7');
      assert.equal(options.headers['X-Briva-Company'], encodeURIComponent('BRIVA İstanbul % A'));
      return { ok: true, json: async () => ({ data: { recovery_codes: codes } }) };
    }
    if (++reads === 1) return { ok: true, json: async () => ({ data: accountState }) };
    return { ok: false, status: 503, json: async () => ({ error: { message: hostile } }) };
  });
  await flush();
  const form = h.get('#as-confirm-form');
  form.listeners.get('submit')({ preventDefault() {}, currentTarget: form });
  await flush();
  assert.equal(h.get('#as-recovery-codes').textContent, codes.join('\n'));
  assert.equal(h.get('#as-recovery-section').hidden, false);
  assert.equal(h.get('#as-message').textContent, hostile);
  assert.equal(h.get('#as-message').innerHTML, '');
  h.get('#as-hide-recovery').listeners.get('click')();
  assert.equal(h.get('#as-recovery-codes').textContent, '');
  assert.equal(h.get('#as-recovery-section').hidden, true);
});
