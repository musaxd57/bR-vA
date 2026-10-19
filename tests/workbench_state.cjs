"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
// Load the browser script as CommonJS even if a parent workspace declares ESM.
const Module = require("node:module");
const jsPath = path.join(__dirname, "../static/js/workbench.js");
const compiled = new Module(jsPath, module);
compiled.filename = jsPath;
compiled._compile(fs.readFileSync(jsPath, "utf8"), jsPath);
const workbench = compiled.exports;

const unit = {
  barcode: "BRVU000000000001", kind: "unit", model_name: "Gömlek", color_code: "SY", size: "M",
  pieces_per_unit: 1, components: [{ variant_id: 2, quantity: 1 }],
  prices: { TRY: "100.00", USD: "10.00" }, unit_prices: { TRY: "100.00", USD: "10.00" }
};
const recipe = {
  barcode: "BRVR000000000001", kind: "recipe", model_name: "Gömlek", color_code: "SY",
  pieces_per_unit: 6, components: [{ variant_id: 1, quantity: 1 }, { variant_id: 2, quantity: 2 }, { variant_id: 3, quantity: 2 }, { variant_id: 4, quantity: 1 }],
  prices: { TRY: "600.00", USD: "60.00" }, unit_prices: { TRY: "100.00", USD: "10.00" }
};

test("print guard measures actual SVG bars and independent width/height rather than character count", () => {
  const svg = '<svg viewBox="0 0 180 68" preserveAspectRatio="none"><g fill="black" shape-rendering="crispEdges"><rect x="12" y="4" width="2" height="48"/><rect x="16" y="4" width="1" height="48"/></g></svg>';
  const geometry = workbench.parseBarcodeGeometry(svg);
  assert.deepEqual(geometry, { viewWidth: 180, viewHeight: 68, narrowBar: 1, barHeight: 48, stretch: true });
  const actual = workbench.labelPrintQuality(geometry, "50x30", 203);
  assert.equal(actual.allowed, true);
  assert.ok(actual.pixelsPerNarrowBar > 2.04 && actual.pixelsPerNarrowBar < 2.05);
  assert.ok(Math.abs(actual.barHeightMm - 48 * 11 / 68) < 0.00001);
  const oldAspectRatio = workbench.labelPrintQuality({ ...geometry, stretch: false }, "50x30", 203);
  assert.equal(oldAspectRatio.allowed, false);
  assert.ok(oldAspectRatio.pixelsPerNarrowBar < 1.3);
  for (const invalid of ["", '<svg viewBox="0 0 180 68"/>', '<svg viewBox="0 0 180 68"><g fill="black"><rect width="0" height="48"/></g></svg>']) assert.throws(() => workbench.parseBarcodeGeometry(invalid));
  assert.throws(() => workbench.labelPrintQuality(geometry, "60x40", 206));
});

test("print guard suggests larger profiles and fails closed when even 70mm cannot fit long codes", () => {
  const geometry = { viewWidth: 235, viewHeight: 68, narrowBar: 1, barHeight: 48, stretch: true };
  assert.equal(workbench.labelPrintQuality(geometry, "60x40", 203).allowed, false);
  const suggestions = workbench.labelPrintSuggestions(geometry, 203);
  assert.equal(suggestions[0].profile, "70x40");
  assert.equal(suggestions[0].dpi, 203);
  const long = { ...geometry, viewWidth: 587 };
  assert.deepEqual(workbench.labelPrintSuggestions(long, 300), []);
  assert.equal(workbench.labelPrintQuality(long, "70x40", 300).allowed, false);
  assert.equal(workbench.labelPrintQuality(long, "70x40", 300).minimumOuterWidthMm, 106);
});

test("quantity rejects negative, fractional, scientific, nonfinite and excessive inputs", () => {
  for (const bad of ["-2", "0", "1.5", "1e2", "Infinity", "NaN", "", "1000001", null, true]) {
    assert.throws(() => workbench.parseQuantity(bad), Error, String(bad));
  }
  assert.equal(workbench.parseQuantity("0002"), 2);
  assert.equal(workbench.parseQuantity("0", true), 0);
});

test("money normalizes Turkish decimals without accepting ambiguous thousands or float artifacts", () => {
  assert.equal(workbench.normalizeMoney("1250,5"), "1250.50");
  assert.equal(workbench.moneyMinor("0.29"), 29);
  assert.equal(workbench.moneyString(workbench.moneyMinor("0.10") + workbench.moneyMinor("0.20")), "0.30");
  for (const bad of ["1.000,00", "1,000.00", "1.234", "-1", "Infinity", "NaN", "1e4", "0", ""]) {
    assert.throws(() => workbench.normalizeMoney(bad), Error, bad);
  }
  assert.equal(workbench.normalizeMoney("0", true), "0.00");
});

test("repeated unit scans merge actual quantities and both total and piece count grow together", () => {
  const cart = [];
  workbench.mergeCart(cart, unit, 2);
  workbench.mergeCart(cart, unit, 2);
  assert.equal(cart.length, 1);
  assert.equal(cart[0].quantity, 4);
  assert.equal(workbench.cartPieces(cart[0]), 4);
  assert.equal(workbench.linePreview(cart[0], "TRY").total, 40000);
  assert.deepEqual(workbench.buildSaleItems(cart, "TRY"), [{ barcode: unit.barcode, quantity: 4 }]);
});

test("currency switch uses per-piece target price and never writes full recipe price to unit", () => {
  const cart = [];
  workbench.mergeCart(cart, unit, 1);
  assert.equal(workbench.linePreview(cart[0], "TRY").total, 10000);
  assert.equal(workbench.linePreview(cart[0], "USD").total, 1000);
  workbench.mergeCart(cart, recipe, 2);
  assert.equal(workbench.linePreview(cart[1], "USD").piecePrice, "10.00");
  assert.equal(workbench.linePreview(cart[1], "USD").total, 12000);
  assert.equal(workbench.cartPieces(cart[1]), 12);
});

test("price overrides are per piece, per currency, reasoned and sent only when applied", () => {
  const cart = [];
  workbench.mergeCart(cart, recipe, 2);
  cart[0].overrides.TRY = { price: "90.00", reason: "Toptan anlaşma" };
  assert.equal(workbench.linePreview(cart[0], "TRY").total, 108000);
  assert.equal(workbench.linePreview(cart[0], "USD").total, 12000);
  assert.deepEqual(workbench.buildSaleItems(cart, "TRY"), [{ barcode: recipe.barcode, quantity: 2, unit_price: "90.00", price_reason: "Toptan anlaşma" }]);
  assert.deepEqual(workbench.buildSaleItems(cart, "USD"), [{ barcode: recipe.barcode, quantity: 2 }]);
  cart[0].overrides.TRY.reason = "";
  assert.throws(() => workbench.buildSaleItems(cart, "TRY"), /açıklama/);
});

test("mixed component prices preserve server total without fabricating a uniform piece price", () => {
  const cart = [];
  workbench.mergeCart(cart, Object.assign({}, recipe, { unit_prices: {}, prices: { TRY: "601.00" } }), 2);
  const preview = workbench.linePreview(cart[0], "TRY");
  assert.equal(preview.total, 120200);
  assert.equal(preview.piecePrice, null);
  assert.equal(preview.missing, false);
  assert.equal(workbench.linePreview(cart[0], "EUR").missing, true);
  assert.throws(() => workbench.buildSaleItems(cart, "EUR"), /fiyatı eksik/);
  const divisibleMixed = { target: Object.assign({}, recipe, { unit_prices: { TRY: null }, prices: { TRY: "600.00" } }), quantity: 1, overrides: {} };
  assert.equal(workbench.linePreview(divisibleMixed, "TRY").piecePrice, null, "An exact average is not a real common component price");
  assert.equal(workbench.linePreview(divisibleMixed, "TRY").total, 60000);
});

test("keyset pages append without duplicate IDs and refreshed records replace stale prices", () => {
  const first = [{ id: 1, quantity: 10 }, { id: 2, quantity: 20 }];
  const next = [{ id: 2, quantity: 19 }, { id: 3, quantity: 5 }];
  const merged = workbench.mergeById(first, next);
  assert.deepEqual(merged, [{ id: 1, quantity: 10 }, { id: 2, quantity: 19 }, { id: 3, quantity: 5 }]);
  assert.equal(first[1].quantity, 20, "The previous server page is not mutated");
});

test("equal total receipt exposes missing XL and extra M separately", () => {
  const summary = workbench.receiptSummary([
    { expected: 10, actual: 10 }, { expected: 20, actual: 21 },
    { expected: 20, actual: 20 }, { expected: 10, actual: 9 }
  ]);
  assert.deepEqual(summary, { expected: 60, actual: 60, short: 1, over: 1 });
  assert.throws(() => workbench.receiptSummary([{ expected: 10, actual: -2 }]), Error);
});

test("transfer expands series and additional loose M into exact aggregate variants", () => {
  const lines = [];
  workbench.mergeCart(lines, recipe, 2);
  workbench.mergeCart(lines, unit, 3);
  assert.deepEqual(workbench.expandTransfer(lines), [
    { variant_id: 1, quantity: 2 }, { variant_id: 2, quantity: 7 },
    { variant_id: 3, quantity: 4 }, { variant_id: 4, quantity: 2 }
  ]);
});

test("pick reload restores expected unit quantities, rather than only unpicked remainder", () => {
  const task = { status: "ready", lines: [
    { variant_id: 2, barcode: unit.barcode, quantity: 3, picked: 3, remaining: 0 },
    { variant_id: 4, barcode: "BRVU000000000004", quantity: 1, picked: 1, remaining: 0 }
  ] };
  assert.deepEqual(workbench.pickRestorePlan(task), [{ barcode: unit.barcode, quantity: 3 }, { barcode: "BRVU000000000004", quantity: 1 }]);
  assert.throws(() => workbench.pickRestorePlan({ status: "cancelled", lines: task.lines }), /hazırlanamaz/);
  assert.throws(() => workbench.pickRestorePlan({ status: "picking", lines: [{ variant_id: 99, quantity: 1 }] }), /barkodu alınamadı/);
});

test("idempotency journal preserves exact payload/key after uncertain response and blocks conflicting writes", () => {
  let count = 0;
  const journal = new workbench.MutationJournal(() => "operation-" + ++count);
  const payload = { items: [{ barcode: unit.barcode, quantity: 2 }] };
  const task = journal.begin("sale", "/sales", payload);
  payload.items[0].quantity = 9;
  assert.equal(task.payload.items[0].quantity, 2);
  journal.uncertain();
  assert.equal(journal.current.key, "operation-1");
  assert.throws(() => journal.begin("sale", "/sales", payload), /aynı işlem kimliği/);
  journal.complete();
  assert.equal(journal.begin("sale", "/sales", payload).key, "operation-2");
});

test("uncertain command survives reload under the same actor/company and keeps exact method, payload and key", () => {
  const memory = new Map();
  const storage = { getItem: (key) => memory.get(key) || null, setItem: (key, value) => memory.set(key, value), removeItem: (key) => memory.delete(key) };
  const durable = new workbench.DurableCommands(storage, 7, "BRIVA");
  const command = { key: "36b8b1e7-f849-45b8-bcc9-8c28c76e69ae", path: "/sales", method: "POST", payload: { customer_id: 12, items: [{ barcode: unit.barcode, quantity: 3 }], payment: { amount: "300.00", method: "cash" } } };
  durable.save(command);
  const reload = new workbench.DurableCommands(storage, 7, "BRIVA");
  assert.deepEqual(reload.read(), Object.assign({}, command, { uncertain: true }));
  const journal = new workbench.MutationJournal(() => "unused-key");
  const restored = journal.restore(reload.read());
  assert.equal(restored.key, command.key);
  assert.equal(restored.uncertain, true);
  assert.throws(() => journal.begin("sale", "/sales", {}), /aynı işlem kimliği/);
  reload.clear();
  assert.equal(reload.read(), null);
});

test("dispatch-before-response reload and auth/CSRF failures retain the exact pending key", () => {
  const memory = new Map();
  const storage = { getItem: (key) => memory.get(key) || null, setItem: (key, value) => memory.set(key, value), removeItem: (key) => memory.delete(key) };
  for (const error of [{ status: 401 }, { status: 403 }, { status: 400, code: "csrf_failed" }]) {
    const first = new workbench.DurableCommands(storage, 7, "BRIVA");
    const live = { key: "dispatch-key-" + error.status, scope: "sale", path: "/sales", method: "POST", uncertain: false, payload: { items: [{ barcode: unit.barcode, quantity: 2 }] } };
    first.save(live); // Request is dispatched; no response/catch happens.
    assert.equal(live.uncertain, false, "Live first-attempt status is independent");
    const rawReload = new workbench.DurableCommands(storage, 7, "BRIVA").read();
    assert.equal(rawReload.uncertain, true, "The persisted record is already conservative before dispatch");
    assert.equal(workbench.classifyMutationError(error, rawReload.uncertain), "retry");
    assert.equal(first.read().key, live.key, "A new login/CSRF response does not remove the uncertain key");
    const journal = new workbench.MutationJournal(() => "must-not-allocate");
    journal.restore(rawReload);
    assert.throws(() => journal.begin("sale", "/sales", {}), /aynı işlem kimliği/);
    first.clear();
  }
});

test("another user or company cannot see or replay a pending command through the recovery scope", () => {
  const memory = new Map();
  const reads = [];
  const storage = { getItem: (key) => { reads.push(key); return memory.get(key) || null; }, setItem: (key, value) => memory.set(key, value), removeItem: (key) => memory.delete(key) };
  const owner = new workbench.DurableCommands(storage, 7, "BRIVA");
  owner.save({ key: "pending-key-123", method: "POST", path: "/customers", payload: { name: "Private Customer" } });
  const colleague = new workbench.DurableCommands(storage, 8, "BRIVA");
  const foreignCompany = new workbench.DurableCommands(storage, 7, "OTHER");
  assert.equal(colleague.read(), null);
  assert.equal(foreignCompany.read(), null);
  assert.equal(reads.includes(owner.key), false);
  assert.equal(owner.read().payload.name, "Private Customer");
});

test("corrupt or forbidden recovery command is rejected and unavailable storage prevents a sendable record", () => {
  const storage = { getItem: () => "broken-json", setItem: () => { throw new Error("Quota exceeded"); }, removeItem: () => {} };
  const durable = new workbench.DurableCommands(storage, 7, "BRIVA");
  assert.throws(() => durable.read(), /okunamadı/);
  assert.throws(() => durable.save({ key: "pending-123", path: "/sales", method: "POST", payload: {} }), /gönderilmedi/);
  assert.throws(() => durable.save({ key: "pending-123", path: "/../legacy", method: "POST", payload: {} }), /doğrulanamadı/);
  assert.throws(() => durable.save({ key: "pending-123", path: "/sales", method: "GET", payload: {} }), /doğrulanamadı/);
});

test("rapid barcode events run once in order, including identical intentional scans", async () => {
  const order = [];
  let concurrent = 0;
  let maxConcurrent = 0;
  const queue = new workbench.SerialScanQueue(async (event) => {
    concurrent += 1; maxConcurrent = Math.max(concurrent, maxConcurrent);
    await new Promise((resolve) => setImmediate(resolve));
    order.push(event); concurrent -= 1; return event;
  });
  const results = await Promise.all([queue.enqueue("M"), queue.enqueue("M"), queue.enqueue("XL"), queue.enqueue("S")]);
  assert.deepEqual(results, ["M", "M", "XL", "S"]);
  assert.deepEqual(order, results);
  assert.equal(maxConcurrent, 1);
  assert.equal(queue.running, false);
});

test("unknown barcode failure does not discard subsequent valid scan events", async () => {
  const queue = new workbench.SerialScanQueue(async (event) => { if (event === "unknown") throw new Error("Barkod yok"); return event; });
  const results = await Promise.allSettled([queue.enqueue("unknown"), queue.enqueue("valid")]);
  assert.equal(results[0].status, "rejected");
  assert.equal(results[1].value, "valid");
  assert.equal(queue.running, false);
});

test("mutation result classification preserves uncertain auth, retryable status and key conflicts", () => {
  assert.equal(workbench.classifyMutationError(new Error("offline"), false), "retry");
  for (const status of [500, 502, 503]) assert.equal(workbench.classifyMutationError({ status }, false), "retry");
  assert.equal(workbench.classifyMutationError({ status: 409, code: "request_in_progress" }, false), "retry");
  for (const status of [401, 403]) {
    assert.equal(workbench.classifyMutationError({ status }, true), "retry");
    assert.equal(workbench.classifyMutationError({ status }, false), "terminal");
  }
  for (const code of ["idempotency_conflict", "idempotency_owner", "auth_context_changed"]) assert.equal(workbench.classifyMutationError({ status: 409, code }, true), "review");
  assert.equal(workbench.classifyMutationError({ status: 400, code: "csrf_failed" }, true), "retry");
  assert.equal(workbench.classifyMutationError({ status: 400, code: "csrf_failed" }, false), "terminal");
  for (const code of ["insufficient_stock", "pricing_changed", "invalid_quantity"]) assert.equal(workbench.classifyMutationError({ status: 409, code }, true), "terminal");
});

test("missing list-currency price cannot be bypassed by a client-only manual price", () => {
  const cart = [];
  workbench.mergeCart(cart, unit, 1);
  cart[0].overrides.EUR = { price: "9.00", reason: "Özel fiyat" };
  assert.equal(workbench.linePreview(cart[0], "EUR").missing, true);
  assert.throws(() => workbench.buildSaleItems(cart, "EUR"), /kataloğunda/);
});

test("size grid rejects duplicate/empty dimensions before generating variant matrix", () => {
  assert.deepEqual(workbench.parseSizes("s, m; L\nXL"), ["S", "M", "L", "XL"]);
  assert.throws(() => workbench.parseSizes("M, M"), /birden fazla/);
  assert.throws(() => workbench.parseSizes(""), /beden/);
});

test("workbench has no HTML injection sinks, inline event handlers or third-party script dependency", () => {
  const js = fs.readFileSync(path.join(__dirname, "../static/js/workbench.js"), "utf8");
  const html = fs.readFileSync(path.join(__dirname, "../templates/workbench.html"), "utf8");
  assert.doesNotMatch(js, /\b(?:innerHTML|outerHTML|insertAdjacentHTML|eval)\b|new\s+Function\s*\(/);
  assert.doesNotMatch(html, /\son(?:click|load|error|submit)\s*=/i);
  assert.doesNotMatch(html, /<script[^>]+src=["']https?:/i);
  assert.match(js, /Idempotency-Key/);
  assert.match(js, /X-CSRFToken/);
  assert.doesNotMatch(js, /localStorage\./);
  assert.match(js, /new DurableCommands\(root\.sessionStorage/);
});

test("literal UI controls resolve to real template IDs and navigation panels", () => {
  const js = fs.readFileSync(jsPath, "utf8");
  const html = fs.readFileSync(path.join(__dirname, "../templates/workbench.html"), "utf8");
  const ids = Array.from(html.matchAll(/\bid="([^"]+)"/g), (match) => match[1]);
  assert.equal(new Set(ids).size, ids.length, "Template IDs must be unique");
  const dynamic = new Set(["bw-pick-scan-form"]);
  const references = Array.from(js.matchAll(/["']#(bw-[a-z0-9-]+)/g), (match) => match[1]);
  for (const reference of references) assert.ok(ids.includes(reference) || dynamic.has(reference), "Missing control: " + reference);
  const views = Array.from(html.matchAll(/\bdata-view="([^"]+)"/g), (match) => match[1]);
  for (const view of views) assert.ok(html.includes('data-panel="' + view + '"'), "Missing panel: " + view);
});
