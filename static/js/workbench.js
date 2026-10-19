/* BRIVA workbench. The server owns stock, pricing and ledger calculations. */
(function (root) {
  "use strict";

  const MAX_QUANTITY = 1000000;
  const CURRENCIES = ["TRY", "USD", "EUR"];
  const LABEL_PROFILES = {
    "50x30": { widthMm: 50, heightMm: 30, paddingMm: 2, imageHeightMm: 11 },
    "60x40": { widthMm: 60, heightMm: 40, paddingMm: 3, imageHeightMm: 17 },
    "70x40": { widthMm: 70, heightMm: 40, paddingMm: 3, imageHeightMm: 17 }
  };
  const VIEWS = {
    overview: ["İŞİN BUGÜNKÜ GÖRÜNÜMÜ", "Her parça yerli yerinde.", "Ürün, barkod, stok ve satış aynı kayıt üzerinden ilerler.", "Genel bakış"],
    catalog: ["KALICI ÜRÜN KİMLİKLERİ", "Bir model, tüm varyantlar.", "Renkleri ve bedenleri tanımla. Her eşleşmenin barkodu sıfır stokla oluşur.", "Ürünler & seriler"],
    receipt: ["ATÖLYEDEN STOĞA", "Gerçek geleni kaydet.", "Seri ve teklemeleri birlikte kabul et; her bedenin eksiğini ve fazlasını gör.", "Mal kabul"],
    inventory: ["HER KONUMDA DOĞRU MİKTAR", "Stok sayısı, beden beden.", "Fiziksel stok, rezervasyon, tam seri ve kalan parçalar bir arada.", "Stok & konumlar"],
    sale: ["BARKODDAN SATIŞA", "Doğru ürün. Doğru adet.", "Tekli veya seri barkodunu okut; stok çıkışını sistem gerçek bedenlere uygulasın.", "Satış yap"],
    picking: ["MİKTARLI DOĞRULAMA", "Rafı bul. Parçayı doğrula.", "Beklenen, okutulan ve kalan miktar sunucuda takip edilir.", "Raf toplama"],
    customers: ["SATIŞLA AYNI HAREKET DEFTERİ", "Cari hesap açık olsun.", "Satış, tahsilat, geri ödeme ve müşteri bakiyesini birlikte izle.", "Müşteri & cari"],
    labels: ["ATÖLYEYE HAZIR", "Her kıyafete doğru etiket.", "Tekli ve seri etiketlerini ayrı hazırla. Baskı yapmak stok miktarını değiştirmez.", "Etiket merkezi"],
    history: ["İZLENEBİLİR İŞLEMLER", "Ne, ne zaman, ne kadar?", "Onaylanan satış ve mal kabul kayıtlarını incele; iadeyi özgün satışa bağla.", "İşlem geçmişi"]
  };

  function parseQuantity(value, allowZero) {
    const text = String(value == null ? "" : value).trim();
    if (!/^\d+$/.test(text)) throw new Error("Adet, kesirsiz ve pozitif bir tam sayı olmalı.");
    const number = Number(text);
    if (!Number.isSafeInteger(number) || number > MAX_QUANTITY || number < (allowZero ? 0 : 1)) {
      throw new Error("Adet " + (allowZero ? "0" : "1") + " ile " + MAX_QUANTITY + " arasında olmalı.");
    }
    return number;
  }

  function normalizeMoney(value, allowZero) {
    const text = String(value == null ? "" : value).trim().replace(",", ".");
    if (!/^\d{1,10}(?:\.\d{1,2})?$/.test(text)) throw new Error("Tutarı binlik ayırıcı olmadan ve en fazla iki ondalıkla yaz. Örn. 1250,50.");
    const parts = text.split(".");
    const whole = Number(parts[0]);
    const fraction = Number((parts[1] || "").padEnd(2, "0"));
    const minor = whole * 100 + fraction;
    if (!Number.isSafeInteger(minor) || minor < (allowZero ? 0 : 1)) throw new Error("Tutar sıfırdan büyük olmalı.");
    return String(whole) + "." + String(fraction).padStart(2, "0");
  }

  function moneyMinor(value) {
    const normalized = normalizeMoney(value, true).split(".");
    return Number(normalized[0]) * 100 + Number(normalized[1]);
  }

  function moneyString(minor) {
    if (!Number.isSafeInteger(minor) || minor < 0) throw new Error("Tutar hesaplanamadı. Fiyat ve adedi kontrol et.");
    return String(Math.floor(minor / 100)) + "." + String(minor % 100).padStart(2, "0");
  }

  function parseSizes(value) {
    const sizes = String(value).split(/[,;\n]/).map(function (part) { return part.trim().toUpperCase(); }).filter(Boolean);
    if (!sizes.length || sizes.length > 40) throw new Error("1 ile 40 arasında beden tanımla.");
    if (sizes.some(function (size) { return size.length > 20; })) throw new Error("Beden adı en fazla 20 karakter olabilir.");
    if (new Set(sizes).size !== sizes.length) throw new Error("Aynı beden birden fazla yazılmış.");
    return sizes;
  }

  function mergeById(existing, incoming) {
    const records = new Map();
    (existing || []).forEach(function (item) { records.set(String(item.id), item); });
    (incoming || []).forEach(function (item) { records.set(String(item.id), item); });
    return Array.from(records.values());
  }

  function parseBarcodeGeometry(svg) {
    if (typeof svg !== "string" || svg.length > 100000) throw new Error("Barkod görselinin ölçüleri doğrulanamadı.");
    const root = /<svg\b([^>]*)>/.exec(svg);
    const box = root && /viewBox="0\s+0\s+([\d.]+)\s+([\d.]+)"/.exec(root[1]);
    const bars = /<g\b[^>]*fill="black"[^>]*>([\s\S]*?)<\/g>/.exec(svg);
    if (!box || !bars) throw new Error("Barkod görselinde doğrulanabilir çizgi ölçüleri yok.");
    const rectangles = Array.from(bars[1].matchAll(/<rect\b([^>]*)\/?\s*>/g));
    const widths = []; const heights = [];
    rectangles.forEach(function (match) { const width = /\bwidth="([\d.]+)"/.exec(match[1]); const height = /\bheight="([\d.]+)"/.exec(match[1]); if (width && height) { widths.push(Number(width[1])); heights.push(Number(height[1])); } });
    const geometry = { viewWidth: Number(box[1]), viewHeight: Number(box[2]), narrowBar: Math.min.apply(Math, widths), barHeight: Math.min.apply(Math, heights), stretch: /preserveAspectRatio="none"/.test(root[1]) };
    if (![geometry.viewWidth, geometry.viewHeight, geometry.narrowBar, geometry.barHeight].every(function (number) { return Number.isFinite(number) && number > 0; })) throw new Error("Barkod çizgi ölçüleri geçersiz; baskı hazırlanmadı.");
    return geometry;
  }

  function labelPrintQuality(geometry, profileName, dpi) {
    const profile = LABEL_PROFILES[profileName]; dpi = Number(dpi);
    if (!profile || ![203, 300].includes(dpi)) throw new Error("Etiket ölçüsünü ve yazıcının 203/300 DPI değerini seç.");
    const innerWidth = profile.widthMm - 2 * profile.paddingMm;
    const horizontal = innerWidth * dpi / 25.4 / geometry.viewWidth;
    const vertical = profile.imageHeightMm * dpi / 25.4 / geometry.viewHeight;
    const scaleX = geometry.stretch ? horizontal : Math.min(horizontal, vertical);
    const scaleY = geometry.stretch ? vertical : scaleX;
    const pixels = scaleX * geometry.narrowBar;
    return { profile: profileName, dpi: dpi, moduleCount: geometry.viewWidth / geometry.narrowBar, pixelsPerNarrowBar: pixels, barHeightMm: geometry.barHeight * scaleY * 25.4 / dpi, allowed: pixels >= 2, minimumOuterWidthMm: Math.ceil(geometry.viewWidth / geometry.narrowBar * 2 * 25.4 / dpi + 6) };
  }

  function labelPrintSuggestions(geometry, dpi) {
    const suggestions = [];
    [Number(dpi)].concat(Number(dpi) === 203 ? [300] : []).forEach(function (candidateDpi) {
      Object.keys(LABEL_PROFILES).forEach(function (profile) { const quality = labelPrintQuality(geometry, profile, candidateDpi); if (quality.allowed) suggestions.push(quality); });
    });
    return suggestions;
  }

  function cartPieces(item) { return item.quantity * (Number(item.target.pieces_per_unit) || 1); }

  function linePreview(item, currency) {
    const override = item.overrides && item.overrides[currency];
    const unitPrices = item.target.unit_prices || {};
    const prices = item.target.prices || {};
    const piecesPerUnit = Number(item.target.pieces_per_unit) || 1;
    if (unitPrices[currency] == null && prices[currency] == null) return { total: 0, piecePrice: null, override: false, missing: true };
    if (override && override.price !== "") {
      const price = normalizeMoney(override.price, true);
      return { total: moneyMinor(price) * cartPieces(item), piecePrice: price, override: true, missing: false };
    }
    if (unitPrices[currency] != null) {
      const price = normalizeMoney(unitPrices[currency], true);
      return { total: moneyMinor(price) * cartPieces(item), piecePrice: price, override: false, missing: false };
    }
    if (prices[currency] != null) {
      const totalPerUnit = moneyMinor(prices[currency]);
      return { total: totalPerUnit * item.quantity, piecePrice: item.target.kind === "unit" ? moneyString(totalPerUnit) : null, override: false, missing: false };
    }
    return { total: 0, piecePrice: null, override: false, missing: true };
  }

  function mergeCart(cart, target, quantity) {
    quantity = parseQuantity(quantity);
    const existing = cart.find(function (item) { return item.target.barcode === target.barcode; });
    if (existing) existing.quantity = parseQuantity(existing.quantity + quantity);
    else cart.push({ target: target, quantity: quantity, overrides: {} });
    return cart;
  }

  function buildSaleItems(cart, currency) {
    if (!cart.length) throw new Error("Önce sepete ürün ekle.");
    return cart.map(function (item) {
      const preview = linePreview(item, currency);
      if (preview.missing) throw new Error((item.target.model_name || item.target.barcode) + " için " + currency + " liste fiyatı eksik. Önce ürün kataloğunda para birimi fiyatını tanımla.");
      const body = { barcode: item.target.barcode, quantity: parseQuantity(item.quantity) };
      if (preview.override) {
        const reason = String(item.overrides[currency].reason || "").trim();
        if (reason.length < 3) throw new Error("Değiştirilen fiyat için en az 3 karakterli bir açıklama yaz.");
        body.unit_price = preview.piecePrice;
        body.price_reason = reason;
      }
      return body;
    });
  }

  function receiptSummary(rows) {
    return rows.reduce(function (total, row) {
      const expected = parseQuantity(row.expected, true);
      const actual = parseQuantity(row.actual, true);
      total.expected += expected;
      total.actual += actual;
      total.short += Math.max(0, expected - actual);
      total.over += Math.max(0, actual - expected);
      return total;
    }, { expected: 0, actual: 0, short: 0, over: 0 });
  }

  function expandTransfer(lines) {
    const result = new Map();
    lines.forEach(function (line) {
      (line.target.components || []).forEach(function (component) {
        const quantity = parseQuantity(component.quantity) * parseQuantity(line.quantity);
        result.set(component.variant_id, (result.get(component.variant_id) || 0) + quantity);
      });
    });
    if (!result.size) throw new Error("Transfer için en az bir ürün ekle.");
    return Array.from(result, function (entry) { return { variant_id: entry[0], quantity: parseQuantity(entry[1]) }; });
  }

  function pickRestorePlan(task, barcodeLookup) {
    if (!task || !["picking", "ready", "open", "pending"].includes(task.status)) throw new Error("Bu toplama görevi artık satışa hazırlanamaz.");
    const lines = task.lines || task.items || [];
    if (!lines.length) throw new Error("Toplama görevinin ürün satırları bulunamadı.");
    const plan = new Map();
    lines.forEach(function (line) {
      const code = line.barcode || (barcodeLookup ? barcodeLookup(line.variant_id) : null);
      if (!code) throw new Error("Toplama görevinin beden barkodu alınamadı. Kataloğu yenile.");
      const quantity = parseQuantity(line.quantity || line.expected_quantity);
      plan.set(code, parseQuantity((plan.get(code) || 0) + quantity));
    });
    return Array.from(plan, function (entry) { return { barcode: entry[0], quantity: entry[1] }; });
  }

  class MutationJournal {
    constructor(uuid) { this.uuid = uuid; this.current = null; }
    begin(scope, path, payload) {
      if (this.current) throw new Error(this.current.uncertain ? "Önce sonucu belirsiz işlemi aynı işlem kimliğiyle tekrar dene." : "Devam eden işlem tamamlanana kadar bekle.");
      this.current = { scope: scope, path: path, payload: JSON.parse(JSON.stringify(payload)), key: this.uuid(), uncertain: false };
      return this.current;
    }
    uncertain() { if (this.current) this.current.uncertain = true; }
    restore(task) { if (this.current) throw new Error("Devam eden bir işlem var."); this.current = JSON.parse(JSON.stringify(task)); this.current.uncertain = true; return this.current; }
    complete() { this.current = null; }
  }

  class DurableCommands {
    constructor(storage, actorId, company) {
      if (!Number.isSafeInteger(actorId) || actorId < 1 || !company) throw new Error("İşlem için oturum sahibi doğrulanamadı. Yeniden giriş yap.");
      this.storage = storage; this.actorId = actorId; this.company = String(company);
      this.key = "briva.pending.v2." + encodeURIComponent(this.company) + "." + actorId;
    }
    validate(task) {
      if (!task || !/^[A-Za-z0-9_.:-]{8,100}$/.test(task.key || "") || !/^\/[A-Za-z0-9/_-]+$/.test(task.path || "") || !["POST", "PUT", "PATCH"].includes(task.method || "POST") || !task.payload || typeof task.payload !== "object" || Array.isArray(task.payload)) throw new Error("Bekleyen işlem kaydı doğrulanamadı. Kayıtları denetlemeden yeni işlem gönderilemez.");
      return task;
    }
    save(task) {
      this.validate(task);
      // Persist conservatively before dispatch. A page can close after the
      // request leaves the browser and before any catch/finally runs. The live
      // first attempt may still treat an explicit rejection as definitive;
      // every recovered request must assume the earlier call could have saved.
      const recoverable = Object.assign({}, task, { uncertain: true });
      try { this.storage.setItem(this.key, JSON.stringify({ actorId: this.actorId, company: this.company, task: recoverable })); }
      catch (error) { throw new Error("Tarayıcı işlem kurtarma kaydını saklayamadı. Veri kaybını önlemek için işlem gönderilmedi. Bu site için oturum depolamasını etkinleştir."); }
    }
    read() {
      let raw;
      try { raw = this.storage.getItem(this.key); }
      catch (error) { throw new Error("Tarayıcı işlem kurtarma kaydına erişemiyor. Bu site için oturum depolamasını etkinleştir."); }
      if (!raw) return null;
      let stored;
      try { stored = JSON.parse(raw); } catch (error) { throw new Error("Bekleyen işlem kaydı okunamadı. Kayıtları denetlemeden yeni işlem gönderilemez."); }
      if (stored.actorId !== this.actorId || stored.company !== this.company) throw new Error("Bekleyen işlem bu oturumun sahibine ait değil.");
      return this.validate(stored.task);
    }
    clear() {
      try { this.storage.removeItem(this.key); }
      catch (error) { throw new Error("İşlem kurtarma kaydı temizlenemedi. Aynı işlem kimliğiyle tekrar kontrol et."); }
    }
  }

  class SerialScanQueue {
    constructor(worker, onState) { this.worker = worker; this.onState = onState || function () {}; this.items = []; this.running = false; }
    enqueue(value) {
      if (this.items.length >= 100) return Promise.reject(new Error("Okutma kuyruğu dolu. Ekrandaki sonuçlar tamamlanana kadar bekle."));
      const promise = new Promise((resolve, reject) => { this.items.push({ value: value, resolve: resolve, reject: reject }); });
      this.drain();
      return promise;
    }
    async drain() {
      if (this.running) { this.onState(true, this.items.length); return; }
      this.running = true; this.onState(true, this.items.length);
      while (this.items.length) {
        const item = this.items.shift();
        try { item.resolve(await this.worker(item.value)); }
        catch (error) { item.reject(error); }
        this.onState(true, this.items.length);
      }
      this.running = false; this.onState(false, 0);
    }
  }

  function classifyMutationError(error, alreadyUncertain) {
    const status = error && error.status;
    const code = error && error.code;
    if (["idempotency_conflict", "idempotency_owner", "auth_context_changed"].includes(code)) return "review";
    if (!Number.isInteger(status) || status < 100 || status >= 500 || code === "request_in_progress" || (alreadyUncertain && ([401, 403].includes(status) || code === "csrf_failed"))) return "retry";
    return "terminal";
  }

  const pure = { parseQuantity: parseQuantity, normalizeMoney: normalizeMoney, moneyMinor: moneyMinor, moneyString: moneyString, parseSizes: parseSizes, mergeById: mergeById, parseBarcodeGeometry: parseBarcodeGeometry, labelPrintQuality: labelPrintQuality, labelPrintSuggestions: labelPrintSuggestions, cartPieces: cartPieces, linePreview: linePreview, buildSaleItems: buildSaleItems, mergeCart: mergeCart, receiptSummary: receiptSummary, expandTransfer: expandTransfer, pickRestorePlan: pickRestorePlan, MutationJournal: MutationJournal, DurableCommands: DurableCommands, SerialScanQueue: SerialScanQueue, classifyMutationError: classifyMutationError };
  if (typeof module !== "undefined" && module.exports) module.exports = pure;
  if (!root || !root.document) return;

  const doc = root.document;
  const $ = function (selector, within) { return (within || doc).querySelector(selector); };
  const $$ = function (selector, within) { return Array.from((within || doc).querySelectorAll(selector)); };
  const state = {
    data: { models: [], recipes: [], locations: [], customers: [], sales: [], receipts: [], plans: [], packages: [], permissions: {} },
    stock: { balances: [], capacities: [], totals: {} }, globalTotals: {}, cart: [], transfers: [], tasks: [],
    catalog: { models: [], pagination: {}, query: "" }, modelIndex: new Map(), variantIndex: new Map(), recipeIndex: new Map(),
    receiptExpected: new Map(), receiptActual: new Map(), receiptDefective: new Map(), recipeQuantities: new Map(), labelCounts: new Map(),
    activeView: "overview", activePickId: null, salePickId: null, returnSale: null, editModelId: null, activeCustomerId: null, countSnapshot: null, scanBusy: false, planLoading: false, loaded: false,
    printReady: false, printBroken: false, printBlocked: false, labelPreviewVersion: 0
  };
  const journal = new MutationJournal(function () {
    if (!root.crypto || !root.crypto.randomUUID) throw new Error("Güvenli işlem kimliği oluşturulamıyor. Uygulamayı localhost veya HTTPS üzerinden aç.");
    return root.crypto.randomUUID();
  });
  let retryMutation = null;
  let refreshVersion = 0;
  let stockVersion = 0;
  let catalogVersion = 0;
  let stockMoreBusy = false;
  let stockLoading = false;
  let planSelectionVersion = 0;
  let statusTimer = null;
  let saleScanQueue = null;
  let durableCommands = null;
  let durableBlocked = false;
  const FORM_PERMISSIONS = { "bw-model-form": "catalog_create", "bw-model-edit-form": "catalog_edit", "bw-price-edit-form": "price_override", "bw-recipe-form": "catalog_edit", "bw-barcode-rule-form": "barcode_rules", "bw-receipt-form": "inventory", "bw-plan-form": "inventory", "bw-package-form": "inventory", "bw-count-form": "price_override", "bw-location-form": "inventory", "bw-transfer-form": "inventory", "bw-sale-form": "sales", "bw-customer-form": "customers", "bw-payment-form": "payments", "bw-return-form": "returns", "bw-pick-scan-form": "picking" };
  const SCOPE_FORMS = { model: "bw-model-form", "model-edit": "bw-model-edit-form", "price-edit": "bw-price-edit-form", recipe: "bw-recipe-form", "barcode-rule": "bw-barcode-rule-form", receipt: "bw-receipt-form", plan: "bw-plan-form", package: "bw-package-form", "stock-count": "bw-count-form", location: "bw-location-form", transfer: "bw-transfer-form", sale: "bw-sale-form", customer: "bw-customer-form", payment: "bw-payment-form", return: "bw-return-form", "pick-scan": "bw-pick-scan-form" };
  function can(permission) { return state.loaded && state.data.permissions[permission] === true; }
  function cartLocked() { return Boolean(journal.current && ["sale", "pick-create"].includes(journal.current.scope)); }

  function node(tag, className, text) {
    const element = doc.createElement(tag);
    if (className) element.className = className;
    if (text != null) element.textContent = String(text);
    return element;
  }

  function append(parent) {
    Array.prototype.slice.call(arguments, 1).forEach(function (child) {
      if (child == null) return;
      parent.append(child instanceof root.Node ? child : doc.createTextNode(String(child)));
    });
    return parent;
  }

  function button(text, onClick, className) {
    const control = node("button", className || "bw-btn bw-btn-sm", text);
    control.type = "button";
    control.addEventListener("click", onClick);
    return control;
  }

  function formatMoney(value, currency) {
    const number = typeof value === "number" ? value / 100 : Number(value || 0);
    return new Intl.NumberFormat("tr-TR", { style: "currency", currency: CURRENCIES.includes(currency) ? currency : "TRY", maximumFractionDigits: 2 }).format(number);
  }

  function formatCount(value) { return new Intl.NumberFormat("tr-TR").format(Number(value || 0)); }
  function formatDate(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : new Intl.DateTimeFormat("tr-TR", { dateStyle: "short", timeStyle: "short", timeZone: "Europe/Istanbul" }).format(parsed);
  }
  function modelById(id) { return state.modelIndex.get(String(id)) || state.data.models.find(function (item) { return String(item.id) === String(id); }); }
  function recipeById(id) { return state.recipeIndex.get(String(id)) || state.data.recipes.find(function (item) { return String(item.id) === String(id); }); }
  function locationById(id) { return state.data.locations.find(function (item) { return String(item.id) === String(id); }); }
  function variantById(id) {
    const indexed = state.variantIndex.get(String(id));
    if (indexed) { const model = modelById(indexed.model_id) || {}; return Object.assign({ model_name: model.name || "Model #" + indexed.model_id, model_code: model.model_code || "" }, indexed); }
    for (const model of state.data.models) {
      const variant = (model.variants || []).find(function (item) { return String(item.id) === String(id); });
      if (variant) return Object.assign({ model_id: model.id, model_name: model.name, model_code: model.model_code }, variant);
    }
    return { id: id, size: "?", color_name: "", model_name: "Kayıt " + id, model_code: "" };
  }
  function identityKey() { return String(state.data.company || "") + ":" + String(state.data.user && state.data.user.id || ""); }
  function absorbMetadata(data) {
    (data.models || []).forEach(function (model) { const prior = state.modelIndex.get(String(model.id)) || {}; state.modelIndex.set(String(model.id), Object.assign({}, prior, model)); (model.variants || []).forEach(function (variant) { state.variantIndex.set(String(variant.id), Object.assign({ model_id: model.id }, variant)); }); });
    (data.variants || []).forEach(function (variant) { state.variantIndex.set(String(variant.id), variant); });
    (data.recipes || []).forEach(function (recipe) { state.recipeIndex.set(String(recipe.id), recipe); });
  }
  function absorbComponents(rows) {
    (rows || []).forEach(function (row) {
      if (!row.variant_id || !row.barcode) return;
      const variant = Object.assign({}, state.variantIndex.get(String(row.variant_id)) || {}, { id: row.variant_id });
      ["model_id", "model_code", "model_name", "color_code", "color_name", "size", "barcode", "prices"].forEach(function (key) { if (row[key] != null) variant[key] = row[key]; });
      state.variantIndex.set(String(row.variant_id), variant);
      if (row.model_id) { const model = Object.assign({}, state.modelIndex.get(String(row.model_id)) || {}, { id: row.model_id }); if (row.model_name) model.name = row.model_name; if (row.model_code) model.model_code = row.model_code; state.modelIndex.set(String(row.model_id), model); }
    });
  }
  function absorbPicking(task) {
    absorbComponents(task.lines || task.items || []); absorbComponents(task.allocations || []);
    state.data.locations = mergeById(state.data.locations, task.source_locations || []);
  }
  function locationName(id) { const location = locationById(id); return location ? location.name : "Konum " + id; }
  function field(form, name) { return form.elements.namedItem(name).value.trim(); }
  function idValue(selector, required) {
    const value = $(selector).value;
    if (!value && required) throw new Error("İşlem için bir konum / kayıt seç.");
    return value ? Number(value) : null;
  }

  function setStatus(message, level, sticky) {
    if (statusTimer) root.clearTimeout(statusTimer);
    const container = $("#bw-status");
    container.hidden = false;
    container.dataset.level = level || "success";
    $("#bw-status-text").textContent = message;
    $("#bw-retry").hidden = !retryMutation;
    $("#bw-status-close").hidden = Boolean(retryMutation);
    if (!sticky && !retryMutation && level !== "error") statusTimer = root.setTimeout(function () { container.hidden = true; }, 10000);
  }

  function reportError(error) { setStatus(error && error.message ? error.message : "İşlem tamamlanamadı. Verileri yenileyip tekrar dene.", "error", true); }

  class ApiError extends Error {
    constructor(message, status, details, code) { super(message); this.status = status; this.details = details; this.code = code; }
  }

  async function api(path, options) {
    const config = Object.assign({ method: "GET" }, options || {});
    const headers = { Accept: "application/json" };
    if (config.method !== "GET") {
      const meta = $("meta[name=csrf-token]");
      if (!meta || !meta.content) throw new ApiError("Güvenli oturum doğrulaması eksik. Sayfayı yenileyip tekrar giriş yap.", 403);
      headers["Content-Type"] = "application/json";
      headers["X-CSRFToken"] = meta.content;
      headers["X-CSRF-Token"] = meta.content;
      if (config.key) headers["Idempotency-Key"] = config.key;
      if (config.expectedActor != null && config.expectedCompany != null) {
        headers["X-Briva-Actor"] = String(config.expectedActor);
        headers["X-Briva-Company"] = encodeURIComponent(String(config.expectedCompany));
      }
    }
    const controller = new root.AbortController();
    const timeout = root.setTimeout(function () { controller.abort(); }, 30000);
    let response;
    try {
      response = await root.fetch("/api/v2" + path, {
        method: config.method, credentials: "same-origin", mode: "same-origin", cache: "no-store",
        headers: headers, body: config.body == null ? undefined : JSON.stringify(config.body), signal: controller.signal
      });
    } finally { root.clearTimeout(timeout); }
    if (response.redirected || !(response.headers.get("content-type") || "").includes("application/json")) {
      const status = [401, 403].includes(response.status) ? response.status : response.status >= 500 ? response.status : config.method === "GET" ? 401 : 502;
      throw new ApiError("Oturum veya sunucu yanıtı doğrulanamadı. Sayfayı yenileyip yeniden giriş yap.", status, null, "unexpected_response");
    }
    let result;
    try { result = await response.json(); }
    catch (error) { throw new ApiError("Sunucudan eksik yanıt geldi. İşlemin sonucunu kontrol et.", 502); }
    if (!response.ok) {
      const problem = result.error || {};
      throw new ApiError(problem.message || result.message || "İşlem sunucu tarafından kabul edilmedi.", response.status, problem.details, problem.code || result.code);
    }
    if (!result || typeof result !== "object" || !Object.prototype.hasOwnProperty.call(result, "data")) throw new ApiError("Sunucu yanıtı işlem sözleşmesiyle uyuşmuyor. Aynı işlem kimliğiyle sonucu kontrol et.", 502, null, "unexpected_response");
    return { data: result.data, replayed: Boolean(result.replayed) };
  }

  function formBusy(form, busy) {
    if (!form) return;
    const fieldset = $("fieldset", form);
    const pending = journal.current && SCOPE_FORMS[journal.current.scope] === form.id;
    if (fieldset) fieldset.disabled = busy || Boolean(pending) || (form.id === "bw-receipt-form" && state.planLoading) || Boolean(FORM_PERMISSIONS[form.id] && !can(FORM_PERMISSIONS[form.id]));
    else $$('button[type="submit"]', form).forEach(function (control) { control.disabled = busy; });
    form.setAttribute("aria-busy", String(busy));
  }

  function applyInteractionLocks() {
    const locked = cartLocked();
    Object.keys(FORM_PERMISSIONS).forEach(function (id) { const form = doc.getElementById(id); if (form) formBusy(form, false); });
    $$("#bw-scan-form input, #bw-scan-form button, #bw-cart input, #bw-cart button").forEach(function (control) { control.disabled = locked || !can("sales"); });
    $("#bw-sale-currency").disabled = locked || !can("sales");
    $("#bw-clear-cart").disabled = locked || state.scanBusy || !can("sales");
    $("#bw-create-pick").disabled = Boolean(journal.current) || state.scanBusy || !can("picking");
    $("#bw-refresh").title = journal.current ? "Kayıtları yenilemek bekleyen işlemi kapatmaz. Önce aynı işlem kimliğiyle sonucu doğrula." : "Sunucudan güncel kayıtları getir";
    let ready = state.cart.length > 0;
    try { ready = ready && cartTotal($("#bw-sale-currency").value).missing === 0; } catch (error) { ready = false; }
    $("#bw-checkout").disabled = !ready || Boolean(journal.current) || state.scanBusy || !can("sales");
  }

  async function mutate(scope, path, payload, onSuccess, form, method) {
    let task;
    try {
      if (!durableCommands || durableBlocked) throw new Error("İşlem kurtarma ve oturum doğrulaması tamamlanmadı. Verileri yenile.");
      task = journal.begin(scope, path, payload); task.method = method || "POST"; task.created_at = new Date().toISOString();
      durableCommands.save(task);
    }
    catch (error) { if (task && journal.current === task) journal.complete(); reportError(error); return false; }
    applyInteractionLocks();
    return performMutation(task, onSuccess, form);
  }

  async function performMutation(task, onSuccess, form) {
    if (!task || journal.current !== task) { reportError(new Error("Bekleyen işlem kimliği eşleşmiyor. Verileri yenile.")); return false; }
    const commandActor = durableCommands.actorId;
    const commandCompany = durableCommands.company;
    async function attempt() {
      if (durableBlocked || !durableCommands || durableCommands.actorId !== commandActor || durableCommands.company !== commandCompany || commandActor !== Number(state.data.user && state.data.user.id) || commandCompany !== String(state.data.company)) {
        setStatus("Bekleyen işlem bu oturumda gönderilemez. İlk işlemi başlatan kullanıcıyla yeniden giriş yapıp sayfayı yenile. Eski işlem kaydı korunuyor.", "warning", true);
        return false;
      }
      retryMutation = null;
      $("#bw-retry").hidden = true;
      formBusy(form, true);
      $("#bw-sync-state").textContent = "İşlem sunucuda doğrulanıyor…";
      let result;
      try {
        result = await api(task.path, { method: task.method || "POST", body: task.payload, key: task.key, expectedActor: commandActor, expectedCompany: commandCompany });
      } catch (error) {
        const disposition = classifyMutationError(error, task.uncertain);
        if (disposition !== "terminal") {
          journal.uncertain();
          retryMutation = attempt;
          if (disposition === "review") { task.needs_review = true; try { durableCommands.save(task); } catch (storageError) { durableBlocked = true; } }
          setStatus(error.code === "auth_context_changed" ? "Bu sekmenin oturum sahibi değişti. İlk işlemi başlatan kullanıcıyla yeniden giriş yapıp sayfayı yenile. Eski istek yeni kullanıcı adına çalıştırılmadı; bekleyen kayıt korunuyor." : disposition === "review" ? "Bekleyen işlemin anahtarı ile sunucudaki kayıt uyuşmuyor. Yeni işlem gönderilmedi. İşlem geçmişini denetlemeden bu işlemi yeni anahtarla tekrarlama; bekleyen kayıt korunuyor." : error instanceof ApiError && ([401, 403].includes(error.status) || error.code === "csrf_failed") ? "İşlem sonucunu görmek için aynı kullanıcıyla yeniden giriş yapıp sayfayı yenilemelisin. Bekleyen işlem kimliği korunuyor; yeni bir kayıt gönderilmedi." : "Sunucuya ulaşılamadı veya yanıt tamamlanmadı. İşlem kaydedilmiş olabilir. Aynı işlem kimliğiyle tekrar dene; ikinci kayıt oluşturulmaz.", "warning", true);
        } else {
          try { durableCommands.clear(); journal.complete(); }
          catch (storageError) { journal.uncertain(); retryMutation = attempt; reportError(storageError); formBusy(form, false); return false; }
          if (error.code === "pricing_changed") {
            await refreshCartPrices();
            setStatus("Ürün fiyatı sepet hazırlanırken değişmiş. Güncel fiyatlar yenilendi; ödeme ve toplamı kontrol ederek satışı yeniden onayla. Stok veya cari değişmedi.", "warning", true);
          } else reportError(error);
        }
        formBusy(form, false);
        applyInteractionLocks();
        $("#bw-sync-state").textContent = retryMutation ? "Bir işlemin sonucu bekleniyor" : "İşlem reddedildi; kayıtlar değişmedi";
        return false;
      }
      try { durableCommands.clear(); journal.complete(); }
      catch (error) { journal.uncertain(); retryMutation = attempt; reportError(error); formBusy(form, false); return false; }
      try {
        if (onSuccess) await onSuccess(result.data, result.replayed);
        await loadData(false);
        if (task.scope === "pick-scan" && state.activeView === "picking") { const next = $("#bw-pick-scan-form input"); if (next) next.focus(); }
        return true;
      } catch (error) {
        reportError(new Error("İşlem sunucuda kaydedildi; ekran yenilenemedi. Verileri yenilemeden aynı işlemi yeni kayıt olarak gönderme."));
        return true;
      } finally {
        formBusy(form, false);
        applyInteractionLocks();
        $("#bw-sync-state").textContent = retryMutation ? "Bir işlemin sonucu bekleniyor" : "Sunucuyla eşitlendi";
      }
    }
    return attempt();
  }

  function configureRecovery() {
    const actorId = state.data.user && state.data.user.id;
    try {
      const storage = new DurableCommands(root.sessionStorage, Number(actorId), state.data.company);
      if (durableCommands && durableCommands.key !== storage.key && journal.current) {
        durableBlocked = true; throw new Error("Oturum sahibi değişti. Sayfayı yenilemeden işlem yapılamaz.");
      }
      durableCommands = storage;
      if (journal.current) return;
      const saved = durableCommands.read();
      if (saved) {
        const restored = journal.restore(saved);
        retryMutation = function () { return performMutation(restored, function () { setStatus("Önceki işlemin sonucu aynı işlem kimliğiyle doğrulandı. İkinci bir stok veya cari kaydı oluşturulmadı."); }); };
        setStatus("Bu oturumda sonucu bekleyen bir işlem var. Yeni işlem yapmadan önce aynı işlem kimliğiyle sonucu kontrol et.", "warning", true);
      }
      durableBlocked = false;
    } catch (error) { durableBlocked = true; reportError(error); }
  }

  function empty(container, title, detail, action) {
    const box = node("div", "bw-empty");
    append(box, node("span", "bw-empty-mark", "◇"), node("strong", "", title), node("p", "", detail));
    if (action) box.append(action);
    container.replaceChildren(box);
  }

  function table(columns, rows) {
    const element = node("table");
    const head = node("thead");
    const tr = node("tr");
    columns.forEach(function (column) { const th = node("th", column.className, column.label); th.scope = "col"; tr.append(th); });
    head.append(tr);
    const body = node("tbody");
    rows.forEach(function (row) {
      const line = node("tr");
      columns.forEach(function (column) { const cell = node("td", column.className); const rendered = column.render(row, cell); append(cell, rendered); line.append(cell); });
      body.append(line);
    });
    element.append(head, body);
    return element;
  }

  function fillSelect(selector, items, placeholder, label, force) {
    const select = $(selector);
    if (!select) return;
    const previous = force == null ? select.value : String(force);
    const options = [];
    if (placeholder != null) { const option = node("option", "", placeholder); option.value = ""; options.push(option); }
    items.forEach(function (item) { const option = node("option", "", label(item)); option.value = String(item.id); options.push(option); });
    select.replaceChildren.apply(select, options);
    if (options.some(function (option) { return option.value === previous; })) select.value = previous;
    else if (placeholder == null && items.length) select.value = String(items[0].id);
  }

  function safeView(view, updateHash) {
    if (!VIEWS[view]) view = "overview";
    state.activeView = view;
    $$('[data-panel]').forEach(function (panel) { panel.hidden = panel.dataset.panel !== view; });
    $$('[data-extra-panel]').forEach(function (panel) { panel.hidden = panel.dataset.extraPanel !== view; });
    $$('[data-view]').forEach(function (control) {
      if (control.dataset.view === view) control.setAttribute("aria-current", "page"); else control.removeAttribute("aria-current");
    });
    const copy = VIEWS[view];
    $("#bw-eyebrow").textContent = copy[0]; $("#bw-title").textContent = copy[1]; $("#bw-subtitle").textContent = copy[2]; $("#bw-breadcrumb").textContent = copy[3];
    doc.title = "BRIVA · " + copy[3];
    if (updateHash !== false && root.history.replaceState) root.history.replaceState(null, "", "#" + view);
    if (view === "picking") loadPicking();
  }

  async function loadData(showSuccess) {
    const version = ++refreshVersion;
    catalogVersion += 1;
    $("#bw-refresh").disabled = true;
    $("#bw-sync-state").textContent = "Veriler yükleniyor…";
    try {
      const result = await api("/bootstrap");
      if (version !== refreshVersion) return;
      const previous = state.data;
      const sameIdentity = previous.company === result.data.company && previous.user && result.data.user && previous.user.id === result.data.user.id;
      if (!sameIdentity) {
        state.modelIndex.clear(); state.variantIndex.clear(); state.recipeIndex.clear(); state.catalog = { models: [], pagination: {}, query: "" }; state.cart = []; state.transfers = []; state.tasks = []; state.receiptExpected.clear(); state.receiptActual.clear(); state.receiptDefective.clear(); state.recipeQuantities.clear(); state.activeCustomerId = null; state.salePickId = null; state.activePickId = null; state.countSnapshot = null;
      }
      state.data = Object.assign({ models: [], recipes: [], locations: [], customers: [], sales: [], receipts: [], plans: [], packages: [], permissions: {} }, result.data);
      ["models", "recipes", "locations", "customers", "sales", "receipts", "plans", "packages"].forEach(function (key) { if (!Array.isArray(state.data[key])) state.data[key] = []; });
      state.catalog.models = state.data.models.slice(); state.catalog.pagination = state.data.catalog_pagination || {};
      if (sameIdentity) { state.data.models = mergeById(previous.models, state.data.models); state.data.recipes = mergeById(previous.recipes, state.data.recipes); }
      absorbMetadata(state.data);
      state.data.plans.forEach(function (plan) { absorbComponents(plan.expected); absorbComponents(plan.accepted); absorbComponents(plan.differences); });
      state.data.packages.forEach(function (item) { absorbComponents(item.components); });
      if (Array.isArray(state.data.picking)) { state.tasks = state.data.picking; state.tasks.forEach(absorbPicking); }
      state.loaded = true;
      configureRecovery();
      invalidateSaleDetail();
      renderBootstrap();
      if (state.catalog.query) await loadCatalog(true, state.catalog.query);
      if (state.cart.length && !state.scanBusy && !cartLocked()) await refreshCartPrices();
      await loadStock();
      $("#bw-sync-state").textContent = "Son yenileme " + new Intl.DateTimeFormat("tr-TR", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Istanbul" }).format(new Date());
      if (showSuccess && !journal.current) setStatus("Güncel kayıtlar sunucudan alındı.");
      if (journal.current) $("#bw-sync-state").textContent = "Bekleyen işlem sonucu korunuyor";
    } catch (error) { reportError(error); $("#bw-sync-state").textContent = "Veriler alınamadı"; }
    finally { if (version === refreshVersion) $("#bw-refresh").disabled = false; }
  }

  async function loadStock() {
    const version = ++stockVersion;
    const identity = identityKey();
    const location = $("#bw-stock-location").value;
    const recipe = $("#bw-stock-recipe").value;
    const selectedLocation = location ? locationById(location) : null;
    const descendants = $("#bw-stock-descendants");
    descendants.disabled = !selectedLocation || !["store", "warehouse"].includes(selectedLocation.kind);
    if (descendants.disabled) descendants.checked = false;
    stockLoading = true; $("#bw-stock-refresh").disabled = true; $("#bw-stock-more").disabled = true;
    try {
      const params = new URLSearchParams(); if (location) params.set("location_id", location); if (recipe) params.set("recipe_id", recipe); if (descendants.checked) params.set("include_descendants", "true");
      const result = await api("/stock" + (params.size ? "?" + params.toString() : ""));
      if (version !== stockVersion || identity !== identityKey()) return;
      state.stock = Object.assign({ balances: [], capacities: [], totals: {} }, result.data);
      absorbMetadata(result.data);
      if (!location) state.globalTotals = state.stock.totals;
      else {
        const global = await api("/stock");
        if (version !== stockVersion || identity !== identityKey()) return;
        state.globalTotals = global.data.totals || {};
      }
      state.data.recipes = mergeById(state.data.recipes, result.data.recipes || []);
      fillSelect("#bw-stock-recipe", state.data.recipes, "Yüklenen tüm reçeteler", function (item) { const model = modelById(item.model_id); return (model ? model.model_code : "") + " · " + item.color_code + " · " + item.name; });
      renderStock(); renderOverview();
    } catch (error) { reportError(error); }
    finally { if (version === stockVersion) { stockLoading = false; $("#bw-stock-refresh").disabled = false; $("#bw-stock-more").disabled = false; } }
  }

  async function loadMoreStock() {
    const pagination = state.stock.pagination || {}; if (stockLoading || stockMoreBusy || !pagination.has_more || !pagination.next_after_balance_id) return;
    const version = stockVersion; const identity = identityKey(); stockMoreBusy = true; $("#bw-stock-more").disabled = true;
    try {
      const params = new URLSearchParams({ after_balance_id: String(pagination.next_after_balance_id) }); const location = $("#bw-stock-location").value; const recipe = $("#bw-stock-recipe").value; if (location) params.set("location_id", location); if (recipe) params.set("recipe_id", recipe); if ($("#bw-stock-descendants").checked) params.set("include_descendants", "true");
      const result = await api("/stock?" + params.toString());
      if (version !== stockVersion || identity !== identityKey()) return;
      state.stock.balances = mergeById(state.stock.balances, result.data.balances || []); state.stock.pagination = result.data.pagination || {}; state.stock.totals = result.data.totals || state.stock.totals; absorbMetadata(result.data); renderStock();
    } catch (error) { reportError(error); }
    finally { stockMoreBusy = false; $("#bw-stock-more").disabled = false; }
  }

  async function loadCatalog(reset, queryOverride) {
    const version = ++catalogVersion; const identity = identityKey(); const query = queryOverride == null ? $("#bw-model-search").value.trim() : queryOverride;
    const params = new URLSearchParams({ q: query, limit: "50", variant_budget: "5000" });
    if (!reset && state.catalog.pagination.next_after_model_id) params.set("after_model_id", String(state.catalog.pagination.next_after_model_id));
    $("#bw-catalog-search").disabled = true; $("#bw-catalog-more").disabled = true;
    try {
      const result = await api("/catalog?" + params.toString());
      if (version !== catalogVersion || identity !== identityKey()) return;
      state.catalog.models = reset ? result.data.models || [] : mergeById(state.catalog.models, result.data.models || []); state.catalog.pagination = result.data.pagination || {}; state.catalog.query = query;
      state.data.models = mergeById(state.data.models, result.data.models || []); state.data.recipes = mergeById(state.data.recipes, result.data.recipes || []); absorbMetadata(result.data); renderBootstrap();
    } catch (error) { reportError(error); }
    finally { $("#bw-catalog-search").disabled = false; $("#bw-catalog-more").disabled = false; }
  }

  function renderBootstrap() {
    $("#bw-account-name").textContent = state.data.user && state.data.user.username ? state.data.user.username : "Hesap";
    const models = state.data.models;
    const modelLabel = function (model) { return model.model_code + " · " + model.name; };
    ["#bw-recipe-model", "#bw-receipt-model", "#bw-label-model", "#bw-count-model"].forEach(function (selector) { fillSelect(selector, models, "Ürün modeli seç", modelLabel); });
    const locationLabel = function (location) { return location.code + " · " + location.name; };
    ["#bw-receipt-location", "#bw-transfer-from", "#bw-transfer-to", "#bw-sale-location", "#bw-return-location", "#bw-package-location", "#bw-count-location"].forEach(function (selector) { fillSelect(selector, state.data.locations, "Konum seç", locationLabel); });
    fillSelect("#bw-receipt-quarantine", state.data.locations.filter(function (location) { return location.kind === "quarantine"; }), "Karantina konumu seç", locationLabel);
    fillSelect("#bw-receipt-plan", state.data.plans, "Üretim planına bağlamadan kabul et", function (plan) { return plan.reference + (plan.workshop ? " · " + plan.workshop : ""); });
    fillSelect("#bw-package-recipe", state.data.recipes, "Seri reçetesi seç", function (recipe) { const model = modelById(recipe.model_id); return (model ? model.model_code : "") + " · " + recipe.color_code + " · " + recipeDescription(recipe); });
    fillSelect("#bw-stock-recipe", state.data.recipes, "Yüklenen tüm reçeteler", function (recipe) { const model = modelById(recipe.model_id); return (model ? model.model_code : "") + " · " + recipe.color_code + " · " + recipe.name; });
    fillSelect("#bw-stock-location", state.data.locations, "Tüm konumlar", locationLabel);
    fillSelect("#bw-location-parent", state.data.locations, "Üst konum yok", locationLabel);
    fillSelect("#bw-sale-customer", state.data.customers, "Misafir müşteri · peşin satış", function (customer) { return customer.name; });
    fillSelect("#bw-payment-customer", state.data.customers, "Müşteri seç", function (customer) { return customer.name; });
    const pagination = state.data.catalog_pagination || {};
    const partial = Number(pagination.total_models || 0) > state.data.models.length;
    const notice = $("#bw-catalog-partial-notice"); notice.hidden = !partial;
    notice.replaceChildren(node("span", "", "Ürün seçim listelerinde " + state.data.models.length + " / " + pagination.total_models + " model yüklü. Eksik görünen modeli katalogda sunucuda arayarak veya daha fazla yükleyerek seçim listelerine ekle. "), button("Kataloğa git →", function () { safeView("catalog"); }, "bw-btn bw-btn-quiet bw-btn-sm"));
    renderModelList(); renderRecipeColors(); renderRecipeList(); renderReceiptRecipes(); renderReceiptMatrix(); renderPlans(); renderPackages(); renderLocations(); renderCart(); renderCustomers(); renderLabels(); renderBarcodeRules(); renderHistory(); renderOverview(); applyPermissions();
    if (state.activeView === "picking") renderPicking();
    if (state.activeCustomerId && can("payments")) loadAccount(state.activeCustomerId);
  }

  function applyPermissions() {
    Object.keys(FORM_PERMISSIONS).forEach(function (id) {
      const form = doc.getElementById(id); if (!form) return;
      formBusy(form, false);
      if (!can(FORM_PERMISSIONS[id])) form.title = "Bu işlem için hesabında " + FORM_PERMISSIONS[id] + " yetkisi gerekli.";
      else form.removeAttribute("title");
    });
    $("#bw-create-pick").disabled = !can("picking");
    $("#bw-ledger-load").disabled = !can("inventory");
    $("#bw-audit-load").disabled = !can("audit");
    $("#bw-account-refresh").disabled = !can("payments") || !state.activeCustomerId;
    $("#bw-health-load").disabled = !can("audit");
    applyInteractionLocks();
  }

  function renderOverview() {
    $("#bw-m-models").textContent = formatCount((state.data.catalog_pagination || {}).total_models == null ? state.data.models.length : state.data.catalog_pagination.total_models);
    $("#bw-m-barcodes").textContent = state.data.barcode_count == null ? "≥ " + formatCount(state.data.models.reduce(function (count, model) { return count + (model.variants || []).length; }, 0) + state.data.recipes.length + state.data.locations.length + state.data.packages.length) : formatCount(state.data.barcode_count);
    $("#bw-m-stock").textContent = state.loaded ? formatCount(state.globalTotals.quantity || 0) : "—";
    $("#bw-m-available").textContent = state.loaded ? formatCount(state.globalTotals.available || 0) : "—";
    const hasStock = Number(state.globalTotals.quantity || 0) > 0 || state.data.receipts.length > 0;
    const steps = [
      { title: "Mağaza, depo ve rafı tanımla", detail: "Her stok hareketinin bir konumu olsun.", done: state.data.locations.length > 0, view: "inventory" },
      { title: "Model, renk ve bedenleri oluştur", detail: "Barkodlar sıfır stokla, topluca hazırlansın.", done: state.data.models.length > 0, view: "catalog" },
      { title: "Seri reçeteni ve etiketlerini hazırla", detail: "Tekli etiketle seri paketini ayrı tanımla.", done: state.data.recipes.length > 0, view: "catalog", subtab: "recipe" },
      { title: "Geleni say ve ilk mal kabulü yap", detail: "Teklemeler dahil gerçek beden miktarını kaydet.", done: hasStock, view: "receipt" }
    ];
    const list = $("#bw-steps");
    list.replaceChildren();
    steps.forEach(function (step, index) {
      const li = node("li"); li.dataset.done = String(step.done);
      const copy = append(node("div"), node("strong", "", step.title), node("p", "", step.detail));
      append(li, node("span", "bw-step-number", step.done ? "✓" : index + 1), copy, button(step.done ? "Aç →" : "Başla →", function () { safeView(step.view); if (step.subtab) selectSubtab(step.subtab); }, "bw-btn bw-btn-quiet bw-btn-sm"));
      list.append(li);
    });
    $("#bw-setup-progress").textContent = steps.filter(function (step) { return step.done; }).length + " / 4 adım";
    renderRecent();
  }

  function renderRecent() {
    const records = state.data.sales.map(function (sale) { return { type: "Satış", reference: sale.reference || "#" + sale.id, date: sale.created_at, detail: formatMoney(sale.total, sale.currency), location: locationName(sale.location_id) }; }).concat(state.data.receipts.map(function (receipt) { return { type: "Mal kabul", reference: receipt.reference || "#" + receipt.id, date: receipt.created_at, detail: formatCount(receipt.total_quantity || (receipt.items || []).reduce(function (sum, item) { return sum + Number(item.quantity || 0); }, 0)) + " parça", location: locationName(receipt.location_id) }; }));
    records.sort(function (a, b) { return String(b.date || "").localeCompare(String(a.date || "")); });
    const container = $("#bw-recent");
    if (!records.length) { empty(container, "İlk hareketini bekliyor", "Model ve konumu tanımladıktan sonra mal kabul ile stoğu başlat."); return; }
    container.replaceChildren(table([
      { label: "İşlem", render: function (row) { return node("span", "bw-pill", row.type); } },
      { label: "Referans", render: function (row) { return row.reference; } },
      { label: "Konum", render: function (row) { return row.location; } },
      { label: "Tarih", render: function (row) { return formatDate(row.date); } },
      { label: "Miktar / tutar", className: "bw-num", render: function (row) { return row.detail; } }
    ], records.slice(0, 6)));
  }

  function addColorRow(code, name) {
    const row = node("div", "bw-color-row");
    const codeLabel = node("label", "", "Renk kodu"); const codeInput = node("input"); codeInput.value = code || ""; codeInput.placeholder = "SY"; codeInput.maxLength = 20; codeInput.required = true; codeInput.dataset.colorCode = "";
    const nameLabel = node("label", "", "Renk adı"); const nameInput = node("input"); nameInput.value = name || ""; nameInput.placeholder = "Siyah"; nameInput.maxLength = 80; nameInput.required = true; nameInput.dataset.colorName = "";
    codeLabel.append(codeInput); nameLabel.append(nameInput);
    const remove = button("×", function () { if ($$(".bw-color-row").length === 1) { codeInput.value = ""; nameInput.value = ""; } else row.remove(); renderVariantPreview(); }, "bw-close"); remove.setAttribute("aria-label", "Bu renk satırını kaldır");
    append(row, codeLabel, nameLabel, remove);
    $("#bw-color-rows").append(row);
    codeInput.addEventListener("input", renderVariantPreview); nameInput.addEventListener("input", renderVariantPreview);
    renderVariantPreview();
  }

  function renderVariantPreview() {
    const colors = $$("[data-color-code]").filter(function (input) { return input.value.trim(); }).length;
    try { const sizes = parseSizes($("#bw-model-form").elements.sizes.value); $("#bw-variant-preview").textContent = colors + " renk × " + sizes.length + " beden = " + colors * sizes.length + " varyant"; }
    catch (error) { $("#bw-variant-preview").textContent = error.message; }
  }

  function renderModelList() {
    const query = $("#bw-model-search").value.trim().toLocaleLowerCase("tr");
    const models = state.catalog.models.filter(function (model) { return (model.model_code + " " + model.name).toLocaleLowerCase("tr").includes(query); });
    const pagination = state.catalog.pagination || {};
    const totalModels = pagination.total_models == null ? state.catalog.models.length : pagination.total_models;
    $("#bw-model-count").textContent = "Bu görünümde " + state.catalog.models.length + " / " + totalModels + " model · " + state.catalog.models.reduce(function (sum, model) { return sum + (model.variants || []).length; }, 0) + " yüklenen varyant";
    $("#bw-catalog-pagination-note").textContent = state.catalog.query ? 'Sunucu araması: “' + state.catalog.query + '”. Yazarken filtre yalnız yüklü sonuçlara uygulanır; yeni arama için “Sunucuda ara” kullan.' : pagination.has_more ? "Katalog kısmen yüklendi. Yazarken yüklenen modeller filtrelenir; bütün katalogda aramak için “Sunucuda ara” kullan." : "Bütün katalog modelleri bu görünümde yüklü.";
    $("#bw-catalog-more").hidden = !pagination.has_more;
    const container = $("#bw-model-list");
    if (!models.length) { empty(container, query ? "Yüklenen sonuçlarda eşleşme yok" : "Katalog henüz boş", query ? "Bütün katalogda kontrol etmek için “Sunucuda ara” düğmesini kullan." : "İlk modeli oluştur; renk ve beden barkodları birlikte hazırlansın."); return; }
    container.replaceChildren();
    models.forEach(function (model) {
      const details = node("details", "bw-model-item");
      const summary = node("summary");
      append(summary, node("div", "bw-model-avatar", String(model.model_code || model.name).slice(0, 2).toUpperCase()), append(node("div"), node("strong", "", model.name), node("small", "", model.model_code)), node("span", "", (model.variants || []).length + " varyant +"));
      const content = node("div", "bw-model-detail");
      content.append(table([
        { label: "Renk", render: function (row) { return row.color_name || row.color_code; } },
        { label: "Beden", render: function (row) { return node("strong", "", row.size); } },
        { label: "Tekli barkod", render: function (row) { return node("span", "bw-code", row.barcode); } },
        { label: "Parça fiyatı", className: "bw-num", render: function (row) { return row.prices && row.prices.TRY != null ? formatMoney(row.prices.TRY, "TRY") : "Fiyat eksik"; } }
      ], model.variants || []));
      const edit = button("Model / fiyat düzenle", function () { openModelEdit(model); }, "bw-btn bw-btn-quiet bw-btn-sm"); edit.disabled = !can("catalog_edit") && !can("price_override");
      const photos = node("a", "bw-btn bw-btn-quiet bw-btn-sm", "Fotoğrafları aç →"); photos.href = "/product-photos?model=" + encodeURIComponent(String(model.id)); photos.setAttribute("aria-label", model.model_code + " modelinin fotoğraflarını aç");
      content.append(edit, photos);
      append(details, summary, content); container.append(details);
    });
  }

  function selectSubtab(name) {
    $$("[data-subtab]").forEach(function (control) { control.setAttribute("aria-selected", String(control.dataset.subtab === name)); });
    $("#bw-model-tab").hidden = name !== "model";
    $("#bw-recipe-tab").hidden = name !== "recipe";
  }

  function openModelEdit(model) {
    state.editModelId = model.id; const form = $("#bw-model-edit-form"); form.elements.model_code.value = model.model_code; form.elements.name.value = model.name; form.elements.description.value = model.description || "";
    fillSelect("#bw-price-variant", model.variants || [], null, function (variant) { return (variant.color_name || variant.color_code) + " · " + variant.size + " · " + variant.barcode; });
    renderPriceEdit(); $("#bw-model-edit-card").hidden = false; applyPermissions(); $("#bw-model-edit-card").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderPriceEdit() {
    const variant = variantById($("#bw-price-variant").value); const form = $("#bw-price-edit-form");
    CURRENCIES.forEach(function (currency) { form.elements["price_" + currency].value = (variant.prices || {})[currency] || ""; }); form.elements.reason.value = "";
  }

  function renderBarcodeRules() {
    const rules = state.data.barcode_rules;
    if (!rules) return;
    $("#bw-rule-mode").value = rules.mode; $("#bw-rule-prefix").value = rules.prefix; renderRuleHelp();
    const container = $("#bw-rule-examples"); container.replaceChildren();
    const names = { unit: "Tekli", recipe: "Seri", package: "Fiziksel paket", location: "Konum" };
    Object.keys(rules.examples || {}).forEach(function (kind) { container.append(append(node("div"), node("span", "", names[kind] || kind), node("code", "bw-code", rules.examples[kind]))); });
  }

  function renderRuleHelp() {
    const numeric = $("#bw-rule-mode").value === "numeric";
    $("#bw-rule-prefix").maxLength = numeric ? 3 : 8;
    $("#bw-rule-prefix").inputMode = numeric ? "numeric" : "text";
    $("#bw-rule-help").textContent = numeric ? "Önek tam 3 rakam olmalı. Kod türü, güvenli sayaç ve kontrol basamağı sunucuda eklenir. Aşağıda kayıtlı kuralın örnekleri görünür." : "Önek 2–8 İngilizce harf/rakam olmalı ve harfle başlamalı. Tekli U, seri R, paket P, konum L işareti ve güvenli kimlik sunucuda eklenir. Aşağıdaki örnekler kayıtlı kurala aittir.";
  }

  function renderRecipeColors() {
    const model = modelById($("#bw-recipe-model").value);
    const colors = new Map();
    if (model) (model.variants || []).forEach(function (variant) { colors.set(variant.color_code, { id: variant.color_code, name: variant.color_name || variant.color_code }); });
    fillSelect("#bw-recipe-color", Array.from(colors.values()), "Renk seç", function (color) { return color.id + " · " + color.name; });
    renderRecipeComponents();
  }

  function renderRecipeComponents() {
    const model = modelById($("#bw-recipe-model").value);
    const color = $("#bw-recipe-color").value;
    const container = $("#bw-recipe-components"); container.replaceChildren();
    if (!model || !color) { container.append(node("p", "bw-help", "Beden dağılımı için önce model ve renk seç.")); renderRecipeTotal(); return; }
    (model.variants || []).filter(function (variant) { return variant.color_code === color; }).forEach(function (variant) {
      const label = node("label", "", variant.size); const input = node("input"); input.type = "number"; input.min = "0"; input.max = "1000"; input.step = "1"; input.value = String(state.recipeQuantities.get(variant.id) || 0); input.dataset.variantId = String(variant.id); input.setAttribute("aria-label", variant.size + " beden seri içi adedi"); input.addEventListener("input", function () { state.recipeQuantities.set(variant.id, input.value || 0); renderRecipeTotal(); }); label.append(input); container.append(label);
    });
    renderRecipeTotal();
  }

  function renderRecipeTotal() {
    let total = 0;
    try { $$("#bw-recipe-components input").forEach(function (input) { total += parseQuantity(input.value, true); }); $("#bw-recipe-total").textContent = total + " parça / seri"; }
    catch (error) { $("#bw-recipe-total").textContent = "Beden adetlerini kontrol et"; }
  }

  function recipeDescription(recipe) {
    return (recipe.components || []).map(function (component) { return variantById(component.variant_id).size + ":" + component.quantity; }).join(" / ");
  }

  function renderRecipeList() {
    const container = $("#bw-recipe-list");
    if (!state.data.recipes.length) { empty(container, "Henüz seri reçetesi yok", "S1/M2/L2/XL1 gibi dağılımı kaydet; seri için ayrı barkod oluşsun."); return; }
    container.replaceChildren();
    state.data.recipes.forEach(function (recipe) {
      const model = modelById(recipe.model_id);
      const article = node("article", "bw-recipe-item");
      append(article, node("h3", "", (model ? model.model_code + " · " + model.name : "Model " + recipe.model_id) + " / " + recipe.color_code), node("p", "", recipe.name + " · sürüm " + recipe.version + " · " + recipeDescription(recipe)), node("span", "bw-code", recipe.barcode)); container.append(article);
    });
  }

  function renderReceiptRecipes() {
    const modelId = $("#bw-receipt-model").value;
    fillSelect("#bw-receipt-recipe", state.data.recipes.filter(function (recipe) { return String(recipe.model_id) === modelId; }), "Seri kullanmadan tekleme gir", function (recipe) { return recipe.color_code + " · " + recipe.name + " · " + recipeDescription(recipe); });
  }

  function receiptVariants() {
    const model = modelById($("#bw-receipt-model").value);
    const variants = model ? (model.variants || []).slice() : [];
    const plan = state.data.plans.find(function (item) { return String(item.id) === $("#bw-receipt-plan").value; });
    if (plan) (plan.expected || []).forEach(function (entry) { if (!variants.some(function (variant) { return variant.id === entry.variant_id; })) variants.push(variantById(entry.variant_id)); });
    return variants;
  }

  function renderReceiptMatrix() {
    const container = $("#bw-receipt-matrix");
    const variants = receiptVariants();
    if (!variants.length) { empty(container, "Model seçerek başla", "Bütün renk ve bedenleri aynı tabloda göreceksin."); renderReceiptSummary(); return; }
    container.replaceChildren(table([
      { label: "Model / renk / beden", render: function (variant) { const model = modelById(variant.model_id); return append(node("div"), node("strong", "", (variant.model_code || (model && model.model_code) || "") + " · " + (variant.color_name || variant.color_code) + " · " + variant.size), node("small", "bw-code", variant.barcode)); } },
      { label: "Beklenen", className: "bw-num", render: function (variant) { const input = node("input"); input.type = "number"; input.min = "0"; input.max = String(MAX_QUANTITY); input.value = String(state.receiptExpected.get(variant.id) || 0); input.dataset.receiptExpected = String(variant.id); input.setAttribute("aria-label", (variant.color_name || variant.color_code) + " " + variant.size + " beklenen adet"); input.addEventListener("input", function () { state.receiptExpected.set(variant.id, input.value || 0); renderReceiptSummary(); }); return input; } },
      { label: "Satılabilir kabul", className: "bw-num", render: function (variant) { const input = node("input"); input.type = "number"; input.min = "0"; input.max = String(MAX_QUANTITY); input.step = "1"; input.value = String(state.receiptActual.get(variant.id) || 0); input.dataset.receiptActual = String(variant.id); input.setAttribute("aria-label", (variant.color_name || variant.color_code) + " " + variant.size + " satılabilir kabul adedi"); input.addEventListener("input", function () { state.receiptActual.set(variant.id, input.value || 0); renderReceiptSummary(); }); return input; } },
      { label: "Kusurlu", className: "bw-num", render: function (variant) { const input = node("input"); input.type = "number"; input.min = "0"; input.max = String(MAX_QUANTITY); input.step = "1"; input.value = String(state.receiptDefective.get(variant.id) || 0); input.dataset.receiptDefective = String(variant.id); input.setAttribute("aria-label", (variant.color_name || variant.color_code) + " " + variant.size + " kusurlu adet"); input.addEventListener("input", function () { state.receiptDefective.set(variant.id, input.value || 0); renderReceiptSummary(); }); return input; } },
      { label: "Fark", className: "bw-num", render: function (variant, cell) { cell.dataset.receiptDiff = String(variant.id); return "0"; } }
    ], variants));
    renderReceiptSummary();
  }

  function renderReceiptSummary() {
    const rows = receiptVariants().map(function (variant) { return { id: variant.id, expected: state.receiptExpected.get(variant.id) || 0, actual: state.receiptActual.get(variant.id) || 0, defective: state.receiptDefective.get(variant.id) || 0 }; });
    try {
      let defective = 0;
      const summary = receiptSummary(rows.map(function (row) { const count = parseQuantity(row.defective, true); defective += count; return { expected: row.expected, actual: parseQuantity(row.actual, true) + count }; }));
      rows.forEach(function (row) { const cell = $('[data-receipt-diff="' + row.id + '"]'); if (cell) { const difference = Number(row.actual) + Number(row.defective) - Number(row.expected); cell.textContent = difference > 0 ? "+" + difference : String(difference); cell.className = "bw-num" + (difference > 0 ? " bw-diff-positive" : difference < 0 ? " bw-diff-negative" : ""); } });
      $("#bw-receipt-summary").replaceChildren.apply($("#bw-receipt-summary"), [["Beklenen", summary.expected], ["Satılabilir kabul", summary.actual - defective], ["Kusurlu", defective], ["Fiziksel gelen", summary.actual], ["Eksik", summary.short], ["Fazla", summary.over]].map(function (entry) { return append(node("span", "", entry[0]), node("strong", "", formatCount(entry[1]))); }));
    } catch (error) { $("#bw-receipt-summary").replaceChildren(node("p", "bw-text-danger", error.message)); }
  }

  async function fetchPlanDetail(id) {
    const identity = identityKey(); const result = await api("/production/plans/" + encodeURIComponent(id));
    if (identity !== identityKey()) throw new Error("Oturum değişti. Sayfayı yenile.");
    const plan = result.data; absorbComponents(plan.expected); absorbComponents(plan.accepted); absorbComponents(plan.differences); state.data.plans = mergeById(state.data.plans, [plan]); return plan;
  }

  async function ensureFullModel(id) {
    const existing = state.data.models.find(function (model) { return model.id === Number(id) && Array.isArray(model.variants); });
    if (existing) return existing;
    const identity = identityKey(); const result = await api("/catalog?model_id=" + encodeURIComponent(id));
    if (identity !== identityKey()) throw new Error("Oturum değişti. Sayfayı yenile.");
    const model = (result.data.models || [])[0]; if (!model) throw new Error("Üretim planının modeli bulunamadı.");
    state.data.models = mergeById(state.data.models, result.data.models); state.data.recipes = mergeById(state.data.recipes, result.data.recipes || []); absorbMetadata(result.data);
    ["#bw-recipe-model", "#bw-receipt-model", "#bw-label-model", "#bw-count-model"].forEach(function (selector) { fillSelect(selector, state.data.models, "Ürün modeli seç", function (entry) { return entry.model_code + " · " + entry.name; }); });
    return model;
  }

  async function choosePlan() {
    const selected = $("#bw-receipt-plan").value; const version = ++planSelectionVersion;
    state.planLoading = true; formBusy($("#bw-receipt-form"), true);
    try {
      let plan = state.data.plans.find(function (item) { return String(item.id) === selected; });
      if (plan) { plan = await fetchPlanDetail(plan.id); const first = (plan.expected || [])[0]; if (first && first.model_id) await ensureFullModel(first.model_id); }
      if (version !== planSelectionVersion || selected !== $("#bw-receipt-plan").value) return;
      state.receiptExpected.clear(); state.receiptActual.clear(); state.receiptDefective.clear();
      if (plan) {
        const first = (plan.expected || [])[0]; if (first) $("#bw-receipt-model").value = String(first.model_id || variantById(first.variant_id).model_id);
        const alreadyAccepted = new Map((plan.accepted || []).map(function (row) { return [row.variant_id, row.quantity]; }));
        (plan.expected || []).forEach(function (row) { state.receiptExpected.set(row.variant_id, Math.max(0, Number(row.quantity) - Number(alreadyAccepted.get(row.variant_id) || 0))); }); renderReceiptRecipes();
      }
      renderReceiptMatrix();
    } catch (error) { reportError(error); }
    finally { if (version === planSelectionVersion) { state.planLoading = false; formBusy($("#bw-receipt-form"), false); } }
  }

  function renderPlans() {
    const container = $("#bw-plan-list"); container.replaceChildren();
    if (!state.data.plans.length) { empty(container, "Kayıtlı üretim planı yok", "Beklenen beden matrisini oluşturup atölye referansıyla kaydedebilirsin."); return; }
    state.data.plans.forEach(function (plan) {
      const details = node("details", "bw-model-item");
      const summary = node("summary");
      const expected = (plan.expected || []).reduce(function (sum, row) { return sum + Number(row.quantity); }, 0);
      const accepted = (plan.accepted || []).reduce(function (sum, row) { return sum + Number(row.quantity); }, 0);
      const differences = plan.differences || [];
      const short = differences.reduce(function (sum, row) { return sum + Math.max(0, Number(row.expected) - Number(row.accepted)); }, 0);
      const over = differences.reduce(function (sum, row) { return sum + Math.max(0, Number(row.accepted) - Number(row.expected)); }, 0);
      const defective = differences.reduce(function (sum, row) { return sum + Number(row.defective || 0); }, 0);
      const differenceLabel = short || over || defective ? " · beden farkı: " + short + " eksik / " + over + " fazla" + (defective ? " / " + defective + " kusurlu" : "") : " · beden dağılımı uyumlu";
      append(summary, node("div", "bw-model-avatar", "P"), append(node("div"), node("strong", "", plan.reference), node("small", "", (plan.workshop || "Atölye belirtilmedi") + " · kabul " + accepted + " / " + expected + " parça"), node("small", short ? "bw-text-danger" : "", differenceLabel)), node("span", "", "+"));
      const body = node("div", "bw-model-detail");
      body.append(table([
        { label: "Model / renk / beden", render: function (row) { const variant = variantById(row.variant_id); return variant.model_code + " · " + (variant.color_name || variant.color_code) + " · " + variant.size; } },
        { label: "Plan", className: "bw-num", render: function (row) { return row.expected; } },
        { label: "Kabul", className: "bw-num", render: function (row) { return row.accepted; } },
        { label: "Kusurlu", className: "bw-num", render: function (row) { return row.defective; } },
        { label: "Kabul farkı", className: "bw-num", render: function (row) { return Number(row.difference) > 0 ? "+" + row.difference : row.difference; } }
      ], plan.differences || []));
      details.addEventListener("toggle", async function () {
        if (!details.open || details.dataset.metadataLoaded) return;
        details.dataset.metadataLoaded = "loading";
        try { await fetchPlanDetail(plan.id); const cells = $$("tbody tr td:first-child", body); (plan.differences || []).forEach(function (row, index) { const variant = variantById(row.variant_id); if (cells[index]) cells[index].textContent = variant.model_code + " · " + (variant.color_name || variant.color_code) + " · " + variant.size; }); details.dataset.metadataLoaded = "true"; }
        catch (error) { delete details.dataset.metadataLoaded; reportError(error); }
      });
      const action = button("Bu plana mal kabul yap →", function () { $("#bw-receipt-plan").value = String(plan.id); choosePlan(); safeView("receipt"); $("#bw-receipt-form").scrollIntoView({ behavior: "smooth", block: "start" }); });
      action.disabled = !can("inventory"); body.append(action); append(details, summary, body); container.append(details);
    });
  }

  function renderPackages() {
    const container = $("#bw-package-list");
    if (!state.data.packages.length) { empty(container, "Henüz fiziksel paket kaydı yok", "Kapalı seri paketini ayrı takip etmek istediğinde kayıtlı açık parçaları bir paket altında birleştir."); return; }
    container.replaceChildren(table([
      { label: "Paket barkodu", render: function (item) { return node("span", "bw-code", item.barcode); } },
      { label: "Konum / içerik", render: function (item) { return append(node("div"), node("strong", "", locationName(item.location_id)), node("small", "", item.components.map(function (part) { const variant = variantById(part.variant_id); return variant.barcode ? (variant.color_name || variant.color_code) + " " + variant.size + ":" + part.quantity : "Varyant #" + part.variant_id + ":" + part.quantity; }).join(" / "))); } },
      { label: "Durum", render: function (item) { return node("span", "bw-pill", ({ sealed: "Kapalı", opened: "Açıldı", sold: "Satıldı" })[item.status] || item.status); } },
      { label: "", render: function (item) {
        if (item.status !== "sealed") return "—";
        const actions = node("div", "bw-inline-actions");
        const open = button("Paketi aç", async function () { await mutate("package-open", "/packages/" + item.id + "/open", { reason: "Tekli satış veya sayım için paket açıldı" }, function () { setStatus("Paket açıldı. Fiziksel toplam değişmeden içindeki parçalar tekli satışa açıldı."); }); }); open.disabled = !can("inventory");
        const add = button("Satışa ekle", async function () { try { const target = await resolveBarcode(item.barcode); mergeCart(state.cart, target, 1); $("#bw-sale-location").value = String(item.location_id); state.salePickId = null; renderCart(); safeView("sale"); } catch (error) { reportError(error); } }, "bw-btn bw-btn-quiet bw-btn-sm"); add.disabled = !can("sales");
        const destination = node("select"); destination.setAttribute("aria-label", item.barcode + " paketinin taşınacağı konum"); destination.append(node("option", "", "Taşınacak konum")); destination.firstElementChild.value = "";
        state.data.locations.filter(function (location) { return location.id !== item.location_id && location.kind !== "quarantine"; }).forEach(function (location) { const option = node("option", "", location.code + " · " + location.name); option.value = String(location.id); destination.append(option); }); destination.disabled = !can("inventory");
        const transfer = button("Paketi taşı", async function () { if (!destination.value) { reportError(new Error("Paket için varış konumu seç.")); return; } await mutate("package-transfer", "/packages/" + item.id + "/transfer", { to_location_id: Number(destination.value), reference: "Kapalı paket transferi" }, function () { setStatus("Kapalı paket içeriğiyle birlikte taşındı. Fiziksel stok toplamı değişmedi."); }); }, "bw-btn bw-btn-quiet bw-btn-sm"); transfer.disabled = !can("inventory");
        const detail = button("İçeriği getir", async function () { try { await fetchPackageDetail(item.id); renderPackages(); } catch (error) { reportError(error); } }, "bw-btn bw-btn-quiet bw-btn-sm"); append(actions, open, add, detail, destination, transfer); return actions;
      } }
    ], state.data.packages));
  }

  async function fetchPackageDetail(id) {
    const identity = identityKey(); const result = await api("/packages/" + encodeURIComponent(id));
    if (identity !== identityKey()) throw new Error("Oturum değişti. Sayfayı yenile.");
    absorbComponents(result.data.components); state.data.packages = mergeById(state.data.packages, [result.data]); return result.data;
  }

  function invalidateCount() { state.countSnapshot = null; $("#bw-count-matrix").replaceChildren(); }

  async function loadCount() {
    const locationId = idValue("#bw-count-location", true); const modelId = idValue("#bw-count-model", true); const model = modelById(modelId);
    if (!model) throw new Error("Sayılacak ürün modelini seç.");
    const control = $("#bw-count-load"); control.disabled = true;
    try {
      const result = await api("/stock?location_id=" + encodeURIComponent(locationId) + "&model_id=" + encodeURIComponent(modelId));
      if (Number($("#bw-count-location").value) !== locationId || Number($("#bw-count-model").value) !== modelId) return;
      const balances = new Map((result.data.balances || []).map(function (balance) { return [balance.variant_id, balance]; }));
      state.countSnapshot = { location_id: locationId, model_id: modelId, rows: (model.variants || []).map(function (variant) { const balance = balances.get(variant.id) || {}; return { variant: variant, expected_quantity: Number(balance.quantity || 0), reserved: Number(balance.reserved || 0), packaged: Number(balance.packaged || 0) }; }) };
      $("#bw-count-matrix").replaceChildren(table([
        { label: "Renk / beden", render: function (row) { return (row.variant.color_name || row.variant.color_code) + " · " + row.variant.size; } },
        { label: "Sistemde", className: "bw-num", render: function (row) { return row.expected_quantity; } },
        { label: "Rezerve / pakette", className: "bw-num", render: function (row) { return row.reserved + " / " + row.packaged; } },
        { label: "Fiziksel sayılan", className: "bw-num", render: function (row) { const input = node("input"); input.type = "number"; input.min = "0"; input.max = String(MAX_QUANTITY); input.step = "1"; input.required = true; input.placeholder = "Sayımı yaz"; input.dataset.countVariant = String(row.variant.id); input.setAttribute("aria-label", (row.variant.color_name || row.variant.color_code) + " " + row.variant.size + " fiziksel sayım"); return input; } }
      ], state.countSnapshot.rows));
    } finally { control.disabled = !can("price_override"); }
  }

  async function loadHealth() {
    const control = $("#bw-health-load"); control.disabled = true;
    try {
      const result = await api("/health"); const data = result.data; const container = $("#bw-health-result"); container.replaceChildren();
      container.append(node("p", data.status === "ok" ? "bw-notice" : "bw-notice bw-notice-warning", data.checked_balance_count + " stok satırı kontrol edildi. " + (data.issue_count ? data.issue_count + " tutarsızlık bulundu. Kayıtları incele; otomatik düzeltme yapılmadı." : "Hareket defteri, rezervasyon ve paket miktarları uyumlu.")));
      const names = { ledger_balance_mismatch: "Hareket defteri ile stok miktarı uyuşmuyor", reservation_mismatch: "Rezervasyon miktarı uyuşmuyor", package_mismatch: "Paket içeriği ile ayrılan miktar uyuşmuyor", invalid_balance: "Geçersiz stok miktarı" };
      if ((data.issues || []).length) container.append(table([
        { label: "Sorun", render: function (issue) { return names[issue.code] || issue.code; } },
        { label: "Ürün / beden", render: function (issue) { const variant = variantById(issue.variant_id); return variant.model_code + " · " + variant.size; } },
        { label: "Konum", render: function (issue) { return locationName(issue.location_id); } },
        { label: "Ayrıntı", render: function (issue) { return node("pre", "bw-audit-detail", JSON.stringify(issue, null, 2)); } }
      ], data.issues));
    } catch (error) { reportError(error); }
    finally { control.disabled = !can("audit"); }
  }

  function renderStock() {
    const totals = state.stock.totals;
    const scope = state.stock.scope || {};
    $("#bw-stock-scope").textContent = scope.include_descendants ? locationName(scope.location_id) + " ve bağlı raflar: " + (scope.physical_location_ids || []).length + " fiziksel konum. Karantina fiziksel toplama dahildir; satılabilir miktara dahil değildir. Seri kapasitesi raflardan toplanabilir miktarı gösterir; satış için raf toplama görevi oluştur." : scope.location_id ? locationName(scope.location_id) + " konumunun kendi stoku gösteriliyor. Bağlı raflar bu toplama dahil değil." : "Tüm konumlar ayrı satırlarda gösteriliyor. Aynı parçanın miktarı tek fiziksel konumda tutulur; farklı rafların seri kapasiteleri birbirine eklenmez.";
    $("#bw-stock-summary").replaceChildren.apply($("#bw-stock-summary"), [["Fiziksel parça", totals.quantity], ["Rezerve", totals.reserved], ["Kapalı pakette", totals.packaged], ["Karantinada", totals.quarantined], ["Açık kullanılabilir", totals.available]].map(function (entry) { return append(node("div"), node("span", "", entry[0]), node("strong", "", formatCount(entry[1]))); }));
    const query = $("#bw-stock-search").value.trim().toLocaleLowerCase("tr");
    const balances = (state.stock.balances || []).map(function (balance) { return Object.assign({}, balance, { variant: variantById(balance.variant_id) }); }).filter(function (row) { return (row.variant.model_name + " " + row.variant.model_code + " " + row.variant.color_name + " " + row.variant.color_code + " " + row.variant.size + " " + row.variant.barcode).toLocaleLowerCase("tr").includes(query); });
    const container = $("#bw-stock-table");
    const pagination = state.stock.pagination || {};
    $("#bw-stock-pagination-note").textContent = (state.stock.balances || []).length + " / " + (pagination.total_balances == null ? (state.stock.balances || []).length : pagination.total_balances) + " stok satırı yüklendi. Arama yüklü satırlarda yapılır; toplam miktarlar tüm seçili konumu kapsar.";
    $("#bw-stock-more").hidden = !pagination.has_more;
    $("#bw-capacity-warning").hidden = !state.stock.capacities_truncated;
    $("#bw-capacity-warning").textContent = "Kapasite görünümü güvenli yükleme sınırına ulaştı. Daha az sonuç için bir konum veya belirli seri reçetesi seç. Gösterilmeyen kapasite sıfır anlamına gelmez.";
    if (!balances.length) empty(container, query ? "Yüklenen satırlarda eşleşme yok" : "Gösterilecek stok hareketi yok", query ? "Daha fazla stok satırı yükle veya aramayı temizleyip konumu daralt." : "Mal kabulden sonra beden miktarları ve seri kapasitesi burada görünür.");
    else container.replaceChildren(table([
      { label: "Ürün", render: function (row) { return append(node("div"), node("strong", "", row.variant.model_name), node("small", "", row.variant.model_code)); } },
      { label: "Renk / beden", render: function (row) { return (row.variant.color_name || row.variant.color_code) + " · " + row.variant.size; } },
      { label: "Konum", render: function (row) { return locationName(row.location_id); } },
      { label: "Fiziksel", className: "bw-num", render: function (row) { return formatCount(row.quantity); } },
      { label: "Rezerve", className: "bw-num", render: function (row) { return formatCount(row.reserved); } },
      { label: "Satılabilir", className: "bw-num", render: function (row) { return node("strong", "", formatCount(row.available)); } }
    ], balances));
    const capacities = $("#bw-capacities");
    if (!(state.stock.capacities || []).length) { empty(capacities, "Seri kapasitesi henüz hesaplanmıyor", "Bir seri reçetesi ve o konumda stok tanımlandığında tam seri ile kalan bedenler görünür."); return; }
    capacities.replaceChildren();
    state.stock.capacities.forEach(function (capacity) {
      const recipe = recipeById(capacity.recipe_id);
      if (!recipe) return;
      const model = modelById(recipe.model_id);
      const card = node("article", "bw-capacity-card");
      const collectable = capacity.capacity_type === "collectable_from_shelves" || capacity.allocation_required;
      append(card, node("h3", "", (model ? model.model_code : "") + " · " + recipe.color_code + " / " + recipe.name), node("p", "", locationName(capacity.location_id) + " · " + recipeDescription(recipe)), append(node("div", "bw-capacity-count"), node("strong", "", formatCount(capacity.series_capacity)), node("span", "", collectable ? "alt raflardan toplanabilir seri" : "tam seri verilebilir")));
      if (collectable) card.append(node("p", "bw-inline-note", "Bu kapasite farklı raflardaki parçalardan oluşabilir. Doğru kaynaklardan çıkış için raf toplama görevi gerekir."));
      const leftovers = node("div", "bw-leftovers");
      (capacity.leftovers || []).forEach(function (item) { leftovers.append(node("span", "", variantById(item.variant_id).size + ": " + formatCount(item.quantity))); });
      if (!leftovers.childNodes.length) leftovers.append(node("span", "", "Seri dışı kalan yok"));
      card.append(node("p", "bw-inline-note", "Seriler ayrıldıktan sonra kalan parçalar"), leftovers);
      if ((capacity.limiting_variant_ids || []).length) card.append(node("div", "bw-limiter", "Sınırlayan beden: " + capacity.limiting_variant_ids.map(function (id) { return variantById(id).size; }).join(", ")));
      capacities.append(card);
    });
  }

  function renderLocations() {
    const container = $("#bw-location-list"); container.replaceChildren();
    const kinds = { store: "Mağaza", warehouse: "Depo", shelf: "Raf", receiving: "Kabul alanı", quarantine: "Karantina" };
    state.data.locations.forEach(function (location) { container.append(append(node("div", "bw-location-row"), append(node("div"), node("strong", "", location.code + " · " + location.name), location.parent_id ? node("small", "", "Üst konum: " + locationName(location.parent_id)) : null), node("span", "bw-pill", kinds[location.kind] || location.kind))); });
  }

  async function resolveBarcode(code) {
    code = String(code || "").trim();
    if (!code || code.length > 80 || !/^[\x21-\x7e]+$/.test(code)) throw new Error("Barkod boş olamaz; yalnız etiket üzerindeki kodu yaz veya okut.");
    const result = await api("/barcodes/" + encodeURIComponent(code));
    const target = result.data;
    if (!target || !target.barcode || !Array.isArray(target.components) || !target.components.length) throw new Error("Barkodun ürün bileşenleri bulunamadı. Ürün tanımını kontrol et.");
    return target;
  }

  async function refreshCartPrices() {
    const results = await Promise.allSettled(state.cart.map(async function (item) { const target = await resolveBarcode(item.target.barcode); item.target = target; }));
    renderCart();
    if (results.some(function (result) { return result.status === "rejected"; })) setStatus("Bazı ürünlerin güncel fiyatı alınamadı. Bağlantıyı kontrol ederek verileri yenile.", "warning", true);
  }

  function targetTitle(target) { return (target.model_name || (modelById(target.model_id) || {}).name || "Ürün") + " · " + (target.color_name || target.color_code || "") + (target.size ? " · " + target.size : ""); }
  function targetDescription(target) {
    const kind = target.kind === "unit" ? "Tekli" : target.kind === "package" ? "Paket" : "Seri";
    return kind + " · " + (Number(target.pieces_per_unit) || 1) + " parça" + (target.kind !== "unit" ? " · " + target.components.map(function (component) { return (component.size || variantById(component.variant_id).size) + ":" + component.quantity; }).join(" / ") : "");
  }

  function cartTotal(currency) {
    return state.cart.reduce(function (summary, item) { const preview = linePreview(item, currency); summary.minor += preview.total; summary.pieces += cartPieces(item); summary.missing += preview.missing ? 1 : 0; return summary; }, { minor: 0, pieces: 0, missing: 0 });
  }

  function renderCart() {
    const container = $("#bw-cart"); const currency = $("#bw-sale-currency").value;
    $("#bw-cart-count").textContent = String(state.cart.length);
    if (!state.cart.length) empty(container, "Sepet boş", "Bir tekli veya seri barkodu okut. Miktarı ve gerekirse parça fiyatını satır üzerinde düzenle.");
    else {
      container.replaceChildren();
      state.cart.forEach(function (item) {
        const preview = linePreview(item, currency);
        const article = node("article", "bw-cart-item");
        const title = node("div", "bw-cart-title");
        append(title, append(node("div"), node("strong", "", targetTitle(item.target)), node("p", "", targetDescription(item.target))), button("×", function () { state.cart = state.cart.filter(function (line) { return line !== item; }); state.salePickId = null; renderCart(); }, "bw-close"));
        title.lastElementChild.setAttribute("aria-label", targetTitle(item.target) + " satırını sepetten kaldır");
        const controls = node("div", "bw-cart-controls");
        const quantityLabel = node("label", "", item.target.kind === "unit" ? "Parça adedi" : "Seri / paket"); const quantity = node("input"); quantity.type = "number"; quantity.min = "1"; quantity.max = String(MAX_QUANTITY); quantity.step = "1"; quantity.value = String(item.quantity); quantity.setAttribute("aria-label", targetTitle(item.target) + " adedi");
        const priceLabel = node("label", "", "Parça fiyatı · " + currency); const price = node("input"); price.inputMode = "decimal"; price.value = preview.piecePrice || ""; price.placeholder = preview.missing ? "Liste fiyatı gerekli" : "Bileşen fiyatları"; price.readOnly = preview.missing || (state.loaded && !can("price_override")); price.title = preview.missing ? "Bu para biriminin fiyatını önce ürün kataloğunda tanımla." : price.readOnly ? "Fiyat değiştirme yetkisi gerekli." : ""; price.setAttribute("aria-label", targetTitle(item.target) + " parça fiyatı");
        const totalNode = node("div", "bw-cart-line-total", preview.missing ? "Fiyat eksik" : formatMoney(preview.total, currency));
        quantityLabel.append(quantity); priceLabel.append(price); append(controls, quantityLabel, priceLabel, totalNode);
        quantity.addEventListener("change", function () { try { item.quantity = parseQuantity(quantity.value); state.salePickId = null; renderCart(); } catch (error) { quantity.value = String(item.quantity); reportError(error); } });
        price.addEventListener("change", function () {
          try {
            if (price.value.trim() === "") delete item.overrides[currency];
            else item.overrides[currency] = { price: normalizeMoney(price.value, true), reason: (item.overrides[currency] || {}).reason || "" };
            renderCart();
          } catch (error) { reportError(error); price.focus(); }
        });
        append(article, title, controls, node("p", "bw-cart-pieces", formatCount(cartPieces(item)) + " parça çıkış yapılacak · " + item.target.barcode));
        if (preview.override) {
          const label = node("label", "bw-price-reason", "Fiyat değişikliği nedeni"); const reason = node("input"); reason.maxLength = 250; reason.placeholder = "Örn. Müşteriye özel anlaşma"; reason.value = item.overrides[currency].reason; reason.addEventListener("input", function () { item.overrides[currency].reason = reason.value; }); label.append(reason); article.append(label);
        }
        container.append(article);
      });
    }
    renderSaleTotals();
    applyInteractionLocks();
  }

  function renderSaleTotals() {
    const currency = $("#bw-sale-currency").value;
    let summary;
    try { summary = cartTotal(currency); }
    catch (error) { reportError(error); return; }
    let paid = summary.minor;
    const method = $("#bw-sale-method").value;
    if (method === "credit") paid = 0;
    else if ($("#bw-sale-payment").value.trim()) { try { paid = moneyMinor($("#bw-sale-payment").value); } catch (error) { paid = 0; } }
    $("#bw-sale-payment").disabled = method === "credit";
    const due = Math.max(0, summary.minor - paid);
    const totals = $("#bw-sale-totals"); totals.replaceChildren();
    [["Toplam parça", formatCount(summary.pieces)], ["Şimdi alınacak", formatMoney(paid, currency)], ["Cariye kalan", formatMoney(due, currency)]].forEach(function (entry) { totals.append(append(node("div", "bw-total-row"), node("span", "", entry[0]), node("strong", "", entry[1]))); });
    totals.append(append(node("div", "bw-total-row bw-grand-total"), node("span", "", "Sepet toplamı"), node("strong", "", formatMoney(summary.minor, currency))));
    if (state.salePickId) totals.append(node("p", "bw-notice", "Toplama görevi #" + state.salePickId + " satışa bağlı. Görevin tam toplanmış olması gerekir."));
    const warning = $("#bw-sale-missing-price"); warning.hidden = !summary.missing; warning.textContent = summary.missing + " satırda " + currency + " liste fiyatı eksik. Ürün kataloğunda bu para birimini tanımla, sonra verileri yenile.";
    $("#bw-checkout").disabled = !state.cart.length || summary.missing > 0 || state.scanBusy || cartLocked();
  }

  function renderTransfers() {
    const container = $("#bw-transfer-lines");
    if (!state.transfers.length) { empty(container, "Transfer sepeti boş", "Taşınacak ürünlerin barkodunu ekle. Seri barkodu gerçek bedenlere açılır."); return; }
    container.replaceChildren(table([
      { label: "Ürün", render: function (line) { return append(node("div"), node("strong", "", targetTitle(line.target)), node("small", "", targetDescription(line.target))); } },
      { label: "Adet", className: "bw-num", render: function (line) { return line.quantity; } },
      { label: "Parça", className: "bw-num", render: function (line) { return line.quantity * (Number(line.target.pieces_per_unit) || 1); } },
      { label: "", render: function (line) { const control = button("×", function () { state.transfers = state.transfers.filter(function (item) { return item !== line; }); renderTransfers(); }, "bw-close"); control.setAttribute("aria-label", "Transfer satırını kaldır"); return control; } }
    ], state.transfers));
  }

  function renderCustomers() {
    const container = $("#bw-customer-list");
    if (!state.data.customers.length) { empty(container, "Henüz müşteri yok", "Peşin satış misafir müşteriye yapılabilir. Veresiye için müşteri kaydı gerekir."); return; }
    container.replaceChildren();
    state.data.customers.forEach(function (customer) {
      const article = node("article", "bw-customer-item"); append(article, node("h3", "", customer.name), customer.phone ? node("p", "", customer.phone) : null);
      const balances = customer.balances || customer.balance || {};
      const row = node("div", "bw-customer-balances");
      if (typeof balances === "object" && !Array.isArray(balances) && Object.keys(balances).length) Object.keys(balances).forEach(function (currency) { const value = balances[currency]; row.append(node("span", "", formatMoney(value, currency) + (Number(value) < 0 ? " · müşteri alacaklı" : Number(value) > 0 ? " · borç" : " · kapalı"))); });
      else row.append(node("span", "", "Bakiye hareketleri kayıtlardan hesaplanır"));
      article.append(row); container.append(article);
      if (can("payments")) article.append(button("Hareket dökümü →", function () { loadAccount(customer.id); }, "bw-btn bw-btn-quiet bw-btn-sm"));
    });
  }

  async function loadAccount(customerId) {
    state.activeCustomerId = customerId; $("#bw-account-refresh").disabled = true;
    try {
      const result = await api("/customers/" + encodeURIComponent(customerId) + "/account");
      if (state.activeCustomerId !== customerId) return;
      $("#bw-account-title").textContent = result.data.customer.name + " · Borç artı, müşteri alacağı eksi tutardır.";
      const container = $("#bw-account-table");
      const movements = result.data.movements || [];
      if (!movements.length) empty(container, "Bu müşterinin hareketi yok", "Satış, tahsilat ve iade hareketleri burada görünür.");
      else container.replaceChildren(table([
        { label: "Tarih", render: function (row) { return formatDate(row.created_at); } },
        { label: "İşlem", render: function (row) { return ({ sale: "Satış", payment: "Tahsilat", refund: "Geri ödeme", return: "İade" })[row.kind] || row.kind; } },
        { label: "Kayıt", render: function (row) { return row.document_type + " #" + row.document_id; } },
        { label: "Kullanıcı", render: function (row) { return "#" + row.actor_id; } },
        { label: "Tutar", className: "bw-num", render: function (row) { return formatMoney(row.amount, row.currency); } }
      ], movements));
    } catch (error) { reportError(error); }
    finally { $("#bw-account-refresh").disabled = !can("payments"); }
  }

  async function loadLedger() {
    const control = $("#bw-ledger-load"); control.disabled = true;
    try {
      const result = await api("/ledger"); const entries = result.data.items || []; const container = $("#bw-ledger-table");
      if (!entries.length) empty(container, "Henüz stok hareketi yok", "Onaylanan işlemler parça, rezervasyon ve paket farkıyla kaydedilir.");
      else container.replaceChildren(table([
        { label: "Tarih / işlem", render: function (entry) { return append(node("div"), node("strong", "", entry.kind), node("small", "", formatDate(entry.created_at))); } },
        { label: "Ürün / beden", render: function (entry) { const variant = variantById(entry.variant_id); return variant.model_code + " · " + (variant.color_name || variant.color_code) + " · " + variant.size; } },
        { label: "Konum", render: function (entry) { return locationName(entry.location_id); } },
        { label: "Parça farkı", className: "bw-num", render: function (entry) { return entry.quantity_delta > 0 ? "+" + entry.quantity_delta : entry.quantity_delta; } },
        { label: "Rezerve farkı", className: "bw-num", render: function (entry) { return entry.reserved_delta > 0 ? "+" + entry.reserved_delta : entry.reserved_delta; } },
        { label: "Paket farkı", className: "bw-num", render: function (entry) { return entry.packaged_delta > 0 ? "+" + entry.packaged_delta : entry.packaged_delta; } },
        { label: "Kayıt / kullanıcı", render: function (entry) { return entry.document_type + " #" + entry.document_id + " / kullanıcı #" + entry.actor_id; } }
      ], entries));
      if (result.data.next_before_id) container.append(node("p", "bw-help", "Son 200 hareket gösteriliyor. Daha eski hareketler sunucuda korunur."));
    } catch (error) { reportError(error); }
    finally { control.disabled = !can("inventory"); }
  }

  async function loadAudit() {
    const control = $("#bw-audit-load"); control.disabled = true;
    try {
      const result = await api("/audit"); const entries = result.data.items || []; const container = $("#bw-audit-table");
      if (!entries.length) empty(container, "Denetim kaydı yok", "Kullanıcı işlemleri yapıldığında burada listelenir.");
      else container.replaceChildren(table([
        { label: "Tarih", render: function (entry) { return formatDate(entry.created_at); } },
        { label: "İşlem", render: function (entry) { return entry.action; } },
        { label: "Kayıt", render: function (entry) { return entry.document_type + " #" + entry.document_id; } },
        { label: "Kullanıcı", render: function (entry) { return "#" + entry.actor_id; } },
        { label: "Ayrıntı", render: function (entry) { const details = node("details"); details.append(node("summary", "", "Ayrıntıyı göster"), node("pre", "bw-audit-detail", JSON.stringify(entry.details || {}, null, 2))); return details; } }
      ], entries));
    } catch (error) { reportError(error); }
    finally { control.disabled = !can("audit"); }
  }

  async function loadPicking() {
    const identity = identityKey();
    try { const result = await api("/picking"); if (identity !== identityKey()) return; state.tasks = Array.isArray(result.data) ? result.data : result.data.tasks || []; state.tasks.forEach(absorbPicking); renderPicking(); }
    catch (error) { reportError(error); }
  }

  function taskStatus(task) { return ({ open: "Toplanıyor", pending: "Bekliyor", picking: "Toplanıyor", ready: "Tam toplandı", completed: "Tamamlandı", fulfilled: "Satıldı", cancelled: "İptal" })[task.status] || task.status || "Açık"; }
  function taskItems(task) { return task.items || task.lines || []; }

  function renderPicking() {
    const container = $("#bw-picking-list");
    if (!state.tasks.length) { empty(container, "Açık toplama görevi yok", "Satış sepetini hazırladıktan sonra o sepet için miktarlı toplama görevi açabilirsin."); return; }
    container.replaceChildren();
    state.tasks.forEach(function (task) {
      const item = node("article", "bw-pick-item"); const counts = taskItems(task).reduce(function (sum, line) { sum.expected += Number(line.quantity || line.expected_quantity || 0); sum.picked += Number(line.picked_quantity || line.picked || 0); return sum; }, { expected: 0, picked: 0 });
      append(item, append(node("div"), node("h3", "", (task.reference || "Görev #" + task.id) + " · " + taskStatus(task)), node("p", "", locationName(task.location_id) + " · " + counts.picked + " / " + counts.expected + " parça")), button("Görevi aç →", function () { state.activePickId = task.id; renderPickDetail(task); })); container.append(item);
    });
    if (state.activePickId) { const active = state.tasks.find(function (task) { return task.id === state.activePickId; }); if (active) renderPickDetail(active); }
  }

  function renderPickDetail(task) {
    absorbPicking(task);
    const container = $("#bw-picking-detail"); container.hidden = false; container.replaceChildren();
    const done = ["cancelled", "fulfilled", "completed"].includes(task.status);
    const head = node("div", "bw-pick-detail-head"); append(head, append(node("div"), node("h2", "", task.reference || "Toplama görevi #" + task.id), node("p", "bw-help", locationName(task.location_id) + " · " + taskStatus(task))), button("Kapat", function () { container.hidden = true; state.activePickId = null; }, "bw-btn bw-btn-quiet bw-btn-sm")); container.append(head);
    const allocationRows = Array.isArray(task.allocations) && task.allocations.length ? task.allocations : taskItems(task);
    container.append(table([
      { label: "Ürün / beden", render: function (line) { const variant = variantById(line.variant_id); return append(node("div"), node("strong", "", variant.model_name + " · " + (variant.color_name || variant.color_code) + " · " + variant.size), node("small", "bw-code", variant.barcode)); } },
      { label: "Kaynak konum", render: function (line) { return locationName(line.location_id || task.location_id); } },
      { label: "Beklenen", className: "bw-num", render: function (line) { return line.quantity || line.expected_quantity || 0; } },
      { label: "Okutulan", className: "bw-num", render: function (line) { return line.picked_quantity || line.picked || 0; } },
      { label: "Kalan", className: "bw-num", render: function (line) { return Math.max(0, Number(line.quantity || line.expected_quantity || 0) - Number(line.picked_quantity || line.picked || 0)); } }
    ], allocationRows));
    if (done) return;
    const taskLocation = locationById(task.location_id);
    if (task.include_descendants) container.append(node("p", "bw-notice" + (!task.active_location_id ? " bw-notice-warning" : ""), (task.active_location_id ? "Aktif kaynak: " + locationName(task.active_location_id) + ". " : "Önce toplanacak kaynak rafın barkodunu okut. ") + "Her raf değişiminde o rafı okut; ardından tekli ürünleri say."));
    else if (taskLocation && taskLocation.kind === "shelf") container.append(node("p", "bw-notice" + (!task.location_verified ? " bw-notice-warning" : ""), (task.location_verified ? "Raf doğrulandı. " : "Önce raf barkodunu okut. ") + "Ardından her kıyafetin tekli barkodunu okut. Raf kodu: " + (task.location_barcode || taskLocation.barcode || "—")));
    const form = node("form"); form.id = "bw-pick-scan-form"; const fieldset = node("fieldset"); const scan = node("div", "bw-scan-row bw-pick-scan-row");
    const label = node("label", "bw-grow", "Raf / tekli ürün barkodu"); const barcode = node("input"); barcode.autocomplete = "off"; barcode.maxLength = 80; barcode.required = true; barcode.placeholder = taskLocation && taskLocation.kind === "shelf" && !task.location_verified ? "Önce raf barkodunu okut" : "Toplanan kıyafetin tekli barkodunu okut"; label.append(barcode);
    const qtyLabel = node("label", "bw-qty-label", "Adet"); const quantity = node("input"); quantity.type = "number"; quantity.min = "1"; quantity.max = String(MAX_QUANTITY); quantity.value = "1"; qtyLabel.append(quantity);
    const submit = node("button", "bw-btn bw-btn-primary", "Doğrula"); submit.type = "submit"; append(scan, label, qtyLabel, submit); fieldset.append(scan); form.append(fieldset); container.append(form);
    form.addEventListener("submit", async function (event) { event.preventDefault(); try { const code = barcode.value.trim(); if (!code) throw new Error("Toplanan ürünün barkodunu okut."); const locationScan = code === task.location_barcode || state.data.locations.some(function (location) { return location.barcode === code; }); const payload = { barcode: code, quantity: parseQuantity(quantity.value) }; await mutate("pick-scan", "/picking/" + task.id + "/scan", payload, async function () { await loadPicking(); setStatus(locationScan ? "Kaynak raf doğrulandı. Ürün miktarı değişmedi; şimdi tekli ürünleri okut." : "Okutulan gerçek miktar toplama görevine işlendi."); const next = $("#bw-pick-scan-form input"); if (next) next.focus(); }, form); } catch (error) { reportError(error); } });
    const actions = node("div", "bw-inline-actions");
    append(actions, button("Mevcut sepeti bu göreve bağla", function () { if (!state.cart.length) { reportError(new Error("Sepet boş. “Görevden satış sepeti hazırla” ile kayıtlı parçalardan yeniden oluşturabilirsin.")); return; } state.salePickId = task.id; $("#bw-sale-location").value = String(task.location_id); safeView("sale"); renderSaleTotals(); setStatus("Toplama görevi #" + task.id + " satışa bağlandı. Sunucu içerik ve tam toplanma durumunu doğrulayacak."); }), button("Görevden satış sepeti hazırla", async function (event) { const control = event.currentTarget; control.disabled = true; try { await restorePickCart(task); } catch (error) { reportError(error); } finally { control.disabled = false; } }), button("Görevi iptal et", async function () { await mutate("pick-cancel", "/picking/" + task.id + "/cancel", {}, async function () { if (state.salePickId === task.id) state.salePickId = null; await loadPicking(); setStatus("Toplama görevi iptal edildi; rezervasyon serbest bırakıldı."); }); }, "bw-btn bw-btn-danger bw-btn-sm")); container.append(actions);
  }

  async function restorePickCart(task) {
    if (!can("sales")) throw new Error("Satış hazırlama yetkin yok.");
    if (journal.current || state.scanBusy) throw new Error("Devam eden işlem ve okutma kuyruğu tamamlanana kadar bekle.");
    if (state.cart.length) throw new Error("Mevcut satış sepeti dolu. Onu boşaltmadan veya satmadan görev sepetiyle değiştirilmez. İçerik aynıysa mevcut sepeti göreve bağlayabilirsin.");
    const plan = pickRestorePlan(task, function (id) { return variantById(id).barcode; });
    const targets = await Promise.all(plan.map(function (line) { return resolveBarcode(line.barcode); }));
    if (state.cart.length || state.scanBusy || journal.current) throw new Error("Bu sırada sepet veya işlem durumu değişti. Mevcut sepet korunuyor.");
    const cart = [];
    targets.forEach(function (target, index) { if (target.kind !== "unit") throw new Error("Görev satırı tekli ürün barkoduna bağlı olmalı."); mergeCart(cart, target, plan[index].quantity); });
    state.cart = cart; state.salePickId = task.id; $("#bw-sale-location").value = String(task.location_id);
    $("#bw-sale-customer").value = ""; $("#bw-sale-payment").value = ""; $("#bw-sale-method").value = "cash";
    renderCart(); safeView("sale");
    setStatus("Görevde kayıtlı gerçek parça miktarları sepete alındı. Güncel liste fiyatlarını kontrol et; önceki manuel fiyatlar taşınmadı. Müşteri ve ödeme alanları sıfırlandı; satışı onaylamadan önce yeniden seç.", "warning", true);
  }

  function labelEntries() {
    const kind = $("#bw-label-kind").value;
    const model = modelById($("#bw-label-model").value);
    if (kind === "location") return state.data.locations.map(function (location) { return { barcode: location.barcode, title: location.code + " · " + location.name, meta: "Konum / raf etiketi", kind: "Konum", size: "Raf / konum", color: location.name }; });
    if (kind === "package") return state.data.packages.filter(function (item) { return item.status === "sealed" && (!model || item.components.some(function (part) { return part.model_id === model.id || variantById(part.variant_id).model_id === model.id; })); }).map(function (item) { const parts = item.components.map(function (part) { const variant = variantById(part.variant_id); return variant.barcode ? variant.color_code + " " + variant.size + ":" + part.quantity : "Varyant #" + part.variant_id + ":" + part.quantity; }).join(" / "); return { barcode: item.barcode, title: "Paket #" + item.id + " · " + locationName(item.location_id), meta: parts, kind: "Fiziksel paket", size: "Kapalı paket", color: locationName(item.location_id) }; });
    if (!model) return [];
    if (kind === "recipe") return state.data.recipes.filter(function (recipe) { return recipe.model_id === model.id; }).map(function (recipe) { return { barcode: recipe.barcode, title: model.model_code + " · " + model.name, meta: recipe.color_code + " · " + recipeDescription(recipe), kind: "Seri reçetesi", size: "Seri", color: recipe.color_code }; });
    return (model.variants || []).map(function (variant) { return { barcode: variant.barcode, title: model.model_code + " · " + model.name, meta: (variant.color_name || variant.color_code) + " · " + variant.size, kind: "Tekli", size: variant.size, color: variant.color_name || variant.color_code }; });
  }

  function invalidatePrint() {
    state.printReady = false; state.printBlocked = false; state.labelPreviewVersion += 1; doc.body.classList.remove("bw-print-ready"); $("#bw-print").disabled = true;
    $("#bw-label-quality").replaceChildren(node("p", "bw-help", "Güncel ölçü ve DPI kontrolü için baskı önizlemesini yeniden hazırla."));
  }

  function renderLabels() {
    invalidatePrint();
    $("#bw-label-model").disabled = $("#bw-label-kind").value === "location";
    const entries = labelEntries(); const container = $("#bw-label-table");
    if (!entries.length) { empty(container, "Etiket için model seç", "Önce model ve beden kimlikleri oluşturulmuş olmalı. Seri etiketi için ayrıca seri reçetesi gerekir."); updateLabelCount(); return; }
    container.replaceChildren(table([
      { label: "Renk", render: function (entry) { return entry.color; } },
      { label: "Beden / birim", render: function (entry) { return entry.size; } },
      { label: "Barkod", render: function (entry) { return node("span", "bw-code", entry.barcode); } },
      { label: "Baskı adedi", className: "bw-num", render: function (entry) { const input = node("input"); input.type = "number"; input.min = "0"; input.max = "500"; input.step = "1"; input.value = String(state.labelCounts.get(entry.barcode) || 0); input.setAttribute("aria-label", entry.meta + " etiket sayısı"); input.addEventListener("input", function () { state.labelCounts.set(entry.barcode, input.value || 0); invalidatePrint(); updateLabelCount(); }); return input; } }
    ], entries));
    updateLabelCount();
  }

  function updateLabelCount() {
    try { const count = labelEntries().reduce(function (sum, entry) { return sum + parseQuantity(state.labelCounts.get(entry.barcode) || 0, true); }, 0); $("#bw-label-count").textContent = count + " etiket seçildi · önizleme başına en fazla 500"; }
    catch (error) { $("#bw-label-count").textContent = error.message; }
  }

  async function fetchBarcodeGeometry(code) {
    const controller = new root.AbortController(); const timeout = root.setTimeout(function () { controller.abort(); }, 20000);
    try {
      const response = await root.fetch("/api/v2/barcodes/" + encodeURIComponent(code) + ".svg", { credentials: "same-origin", mode: "same-origin", cache: "no-store", headers: { Accept: "image/svg+xml" }, signal: controller.signal });
      if (!response.ok || response.redirected || !(response.headers.get("content-type") || "").includes("image/svg+xml")) throw new Error("Barkodun gerçek çizgi ölçüleri alınamadı. Oturumu ve etiket motorunu kontrol et; baskı açılmadı.");
      return parseBarcodeGeometry(await response.text());
    } finally { root.clearTimeout(timeout); }
  }

  function renderLabelQuality(results, profile, dpi) {
    const container = $("#bw-label-quality"); container.replaceChildren(); const blocked = results.filter(function (result) { return !result.quality.allowed; });
    const minimum = Math.min.apply(Math, results.map(function (result) { return result.quality.pixelsPerNarrowBar; }));
    const notice = node("div", "bw-notice" + (blocked.length ? " bw-notice-warning" : ""));
    append(notice, node("strong", "", profile.replace("x", " × ") + " mm · " + dpi + " DPI · en dar çizgi " + minimum.toFixed(3) + " px"), node("p", "", blocked.length ? blocked.length + " farklı barkod 2 piksel eşiğinin altında. Kodlar kırpılmadı; baskı engellendi." : "Bütün barkodlar 2 piksel ölçü eşiğini karşılıyor. Bu, fiziksel yazıcı ve okuyucu doğrulamasının yerine geçmez.")); container.append(notice);
    if (blocked.length) container.append(table([
      { label: "Barkod", render: function (result) { return node("span", "bw-code", result.entry.barcode); } },
      { label: "Dar çizgi", className: "bw-num", render: function (result) { return result.quality.pixelsPerNarrowBar.toFixed(3) + " px"; } },
      { label: "Uygun ölçü önerisi", render: function (result) {
        const suggestions = labelPrintSuggestions(result.geometry, dpi); const suggestion = suggestions[0];
        if (suggestion) return suggestion.profile.replace("x", " × ") + " mm / " + suggestion.dpi + " DPI (" + suggestion.pixelsPerNarrowBar.toFixed(2) + " px)";
        const minimum300 = labelPrintQuality(result.geometry, "70x40", 300).minimumOuterWidthMm;
        return "Hazır şablonların hiçbiri yeterli değil. 300 DPI için en az " + minimum300 + " mm genişlikte ayrı bir etiket şablonu gerekir.";
      } }
    ], blocked));
    container.append(node("p", "bw-help", "Dijital çözümleme referansı: 60 × 40 mm / 300 DPI, örnek etiketler bağımsız ZXing çözücüsüyle okundu. Seçili profil yukarıda ölçülür; gerçek yazıcı, etiket kâğıdı ve fiziksel okuyucu pilotu henüz yapılmadı."));
    return blocked.length > 0;
  }

  async function prepareLabels() {
    invalidatePrint();
    const requestedVersion = state.labelPreviewVersion;
    if ($("#bw-label-kind").value === "package") {
      const packages = state.data.packages.filter(function (item) { return Number(state.labelCounts.get(item.barcode) || 0) > 0; });
      const copies = packages.reduce(function (sum, item) { return sum + parseQuantity(state.labelCounts.get(item.barcode), true); }, 0); if (copies > 500) throw new Error("Bir önizlemede en fazla 500 etiket hazırla.");
      for (let offset = 0; offset < packages.length; offset += 4) await Promise.all(packages.slice(offset, offset + 4).map(function (item) { return fetchPackageDetail(item.id); }));
      if (requestedVersion !== state.labelPreviewVersion) return;
    }
    const entries = labelEntries(); let total = 0;
    const selected = entries.map(function (entry) { const count = parseQuantity(state.labelCounts.get(entry.barcode) || 0, true); total += count; return { entry: entry, count: count }; }).filter(function (item) { return item.count > 0; });
    if (!total) throw new Error("En az bir etikete baskı adedi gir.");
    if (total > 500) throw new Error("Bir önizlemede en fazla 500 etiket hazırla. Daha büyük işleri birkaç partiye böl.");
    const version = state.labelPreviewVersion; const size = $("#bw-label-size").value; const dpi = Number($("#bw-label-dpi").value); const note = $("#bw-label-note").value.trim(); const sheet = $("#bw-print-sheet"); sheet.replaceChildren(); state.printBroken = false;
    $("#bw-label-preview-card").hidden = false; $("#bw-label-print-info").textContent = "Gerçek barkod çizgi ölçüleri ve yazıcı DPI değeri kontrol ediliyor…";
    const qualities = [];
    for (let offset = 0; offset < selected.length; offset += 4) {
      const batch = await Promise.all(selected.slice(offset, offset + 4).map(async function (item) { const geometry = await fetchBarcodeGeometry(item.entry.barcode); return { entry: item.entry, geometry: geometry, quality: labelPrintQuality(geometry, size, dpi) }; })); qualities.push.apply(qualities, batch);
      if (version !== state.labelPreviewVersion) return;
    }
    state.printBlocked = renderLabelQuality(qualities, size, dpi);
    const images = [];
    selected.forEach(function (item) {
      for (let index = 0; index < item.count; index += 1) {
        const label = node("div", "bw-print-label"); label.dataset.size = size;
        const image = node("img"); image.alt = item.entry.barcode + " Code 128 barkodu"; image.width = 210; image.height = 60; image.src = "/api/v2/barcodes/" + encodeURIComponent(item.entry.barcode) + ".svg";
        const loaded = new Promise(function (resolve) { const timer = root.setTimeout(function () { resolve(false); }, 20000); image.addEventListener("load", function () { root.clearTimeout(timer); resolve(true); }, { once: true }); image.addEventListener("error", function () { root.clearTimeout(timer); resolve(false); }, { once: true }); }); images.push(loaded);
        append(label, node("strong", "", item.entry.title), node("div", "bw-label-meta", item.entry.meta), image, node("div", "bw-label-code", item.entry.barcode), note ? node("small", "", note) : null); sheet.append(label);
      }
    });
    $("#bw-label-preview-card").hidden = false; $("#bw-label-print-info").textContent = total + " etiket · " + size.replace("x", " × ") + " mm · Barkod görselleri yükleniyor…";
    const results = await Promise.all(images);
    if (version !== state.labelPreviewVersion) return;
    state.printBroken = results.some(function (loaded) { return !loaded; }); state.printReady = !state.printBroken && !state.printBlocked;
    $("#bw-print").disabled = !state.printReady;
    $("#bw-label-print-info").textContent = total + " etiket · " + size.replace("x", " × ") + " mm / " + dpi + " DPI · " + (state.printBroken ? "Bazı barkodlar yüklenemedi. Yazdırmadan önce önizlemeyi yeniden hazırla." : state.printBlocked ? "Seçili ölçüde baskı engellendi. Yukarıdaki boyut veya DPI önerisini uygula." : "Çizgi ölçüleri kontrol edildi. Önce örnek etiketi fiziksel okuyucuda dene.");
    doc.body.classList.toggle("bw-print-ready", state.printReady);
    if (state.printBroken) throw new Error("Barkod görsellerinin tamamı yüklenmedi. Eksik barkodla baskı yapılamaz.");
  }

  function renderHistory() {
    const sales = $("#bw-sales-history");
    if (!state.data.sales.length) empty(sales, "Henüz satış yok", "Onaylanan ilk satış tutarı ve parça miktarı burada görünür.");
    else sales.replaceChildren(table([
      { label: "Referans / tarih", render: function (sale) { return append(node("div"), node("strong", "", sale.reference || "#" + sale.id), node("small", "", formatDate(sale.created_at))); } },
      { label: "Konum", render: function (sale) { return locationName(sale.location_id); } },
      { label: "Toplam", className: "bw-num", render: function (sale) { return formatMoney(sale.total, sale.currency); } },
      { label: "Satışta alınan", className: "bw-num", render: function (sale) { return formatMoney(sale.paid, sale.currency); } },
      { label: "Satış anında kalan", className: "bw-num", render: function (sale) { return formatMoney(sale.balance_due, sale.currency); } },
      { label: "", render: function (sale) { const actions = node("div", "bw-inline-actions"); const returns = button("İade al", function () { openReturn(sale); }); returns.disabled = !can("returns"); append(actions, button("Ayrıntı", function () { renderSaleDetail(sale); }), returns); return actions; } }
    ], state.data.sales));
    const receipts = $("#bw-receipts-history");
    if (!state.data.receipts.length) empty(receipts, "Henüz mal kabul yok", "Teslim aldığın gerçek beden miktarları referansıyla birlikte burada listelenir.");
    else receipts.replaceChildren(table([
      { label: "Referans", render: function (receipt) { return receipt.reference || "#" + receipt.id; } },
      { label: "Tarih", render: function (receipt) { return formatDate(receipt.created_at); } },
      { label: "Konum", render: function (receipt) { return locationName(receipt.location_id); } },
      { label: "Kabul edilen", className: "bw-num", render: function (receipt) { return formatCount(receipt.total_quantity || (receipt.items || []).reduce(function (sum, item) { return sum + Number(item.quantity || 0); }, 0)) + " parça"; } }
    ], state.data.receipts));
  }

  function saleDocumentLink(saleId) {
    const id = Number(saleId);
    if (!Number.isSafeInteger(id) || id < 1) return node("span", "bw-help", "İşlem belgesi için satış kimliği doğrulanamadı.");
    const link = node("a", "bw-btn bw-btn-quiet bw-btn-sm", "İşlem belgesini aç ↗");
    link.href = "/sale-document/" + id; link.target = "_blank"; link.rel = "noopener noreferrer";
    link.setAttribute("aria-label", "Satış işlem belgesini yeni sekmede aç");
    return link;
  }

  function invalidateSaleDetail() {
    const container = $("#bw-sale-detail");
    if (!container.childNodes.length) return;
    $("#bw-sale-detail-title").textContent = "Güncel satış ayrıntısı yeniden açılmalı.";
    container.replaceChildren(node("p", "bw-help", "İşlem verileri değişmiş olabilir. Güncel iade miktarlarını görmek için satış listesindeki “Ayrıntı” düğmesini yeniden aç."));
  }

  function renderSaleDetail(sale) {
    $("#bw-sale-detail-title").textContent = (sale.reference || "Satış #" + sale.id) + " · " + formatDate(sale.created_at) + " · " + locationName(sale.location_id);
    const container = $("#bw-sale-detail"); container.replaceChildren();
    container.append(table([
      { label: "Satıştaki ürün / beden", render: function (item) { const snapshot = item.snapshot || {}; const variant = variantById(item.variant_id); return append(node("div"), node("strong", "", (snapshot.model_name || variant.model_name) + " · " + (snapshot.color_name || variant.color_name || snapshot.color_code || variant.color_code) + " · " + (snapshot.size || variant.size)), node("small", "bw-code", snapshot.barcode || variant.barcode)); } },
      { label: "Adet", className: "bw-num", render: function (item) { return item.quantity; } },
      { label: "Liste fiyatı", className: "bw-num", render: function (item) { return formatMoney(item.list_price, sale.currency); } },
      { label: "Satılan parça fiyatı", className: "bw-num", render: function (item) { return formatMoney(item.unit_price, sale.currency); } },
      { label: "Satır toplamı", className: "bw-num", render: function (item) { return formatMoney(item.total, sale.currency); } },
      { label: "İade", className: "bw-num", render: function (item) { return item.returned || 0; } },
      { label: "Fiyat değişikliği nedeni", render: function (item) { return item.price_reason || "—"; } }
    ], sale.items || []));
    container.append(node("p", "bw-notice", "Satışta alınan: " + formatMoney(sale.paid, sale.currency) + " · Satış anında cariye kalan: " + formatMoney(sale.balance_due, sale.currency) + ". Sonraki tahsilatlar müşteri genel carisinde izlenir; bu ilk satış tutarlarını değiştirmez."));
    container.append(saleDocumentLink(sale.id));
    safeView("history"); container.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function openReturn(sale) {
    state.returnSale = sale; const container = $("#bw-return-items"); container.replaceChildren();
    $("#bw-return-card").hidden = false; $("#bw-return-reference").textContent = (sale.reference || "Satış #" + sale.id) + " · " + formatMoney(sale.total, sale.currency);
    (sale.items || []).forEach(function (item) {
      const variant = variantById(item.variant_id); const max = Number(item.quantity) - Number(item.returned || item.returned_quantity || 0); const line = node("div", "bw-return-line"); const label = node("label", "", "İade adedi"); const input = node("input"); input.type = "number"; input.min = "0"; input.max = String(max); input.value = "0"; input.dataset.saleItemId = String(item.id); input.setAttribute("aria-label", variant.model_name + " " + variant.size + " iade adedi"); label.append(input);
      append(line, append(node("div"), node("strong", "", variant.model_name + " · " + (variant.color_name || variant.color_code) + " · " + variant.size), node("p", "", "Satış fiyatı " + formatMoney(item.unit_price, sale.currency) + " · iade alınabilir " + max + " parça")), label); container.append(line);
    });
    safeView("history"); $("#bw-return-card").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function onForm(selector, handler) {
    $(selector).addEventListener("submit", async function (event) { event.preventDefault(); try { await handler(event.currentTarget); } catch (error) { reportError(error); } });
  }

  function wireEvents() {
    $$('[data-view]').forEach(function (control) { control.addEventListener("click", function () { safeView(control.dataset.view); }); });
    $$('[data-go]').forEach(function (control) { control.addEventListener("click", function () { safeView(control.dataset.go); }); });
    $$('[data-subtab]').forEach(function (control) { control.addEventListener("click", function () { selectSubtab(control.dataset.subtab); }); });
    $("#bw-refresh").addEventListener("click", function () { loadData(true); });
    $("#bw-status-close").addEventListener("click", function () { $("#bw-status").hidden = true; });
    $("#bw-retry").addEventListener("click", function () { if (retryMutation) retryMutation(); });
    $("#bw-add-color").addEventListener("click", function () { addColorRow(); });
    $("#bw-model-form").elements.sizes.addEventListener("input", renderVariantPreview);
    $("#bw-model-search").addEventListener("input", renderModelList);
    $("#bw-model-search").addEventListener("keydown", function (event) { if (event.key === "Enter") { event.preventDefault(); loadCatalog(true); } });
    $("#bw-catalog-search").addEventListener("click", function () { loadCatalog(true); });
    $("#bw-catalog-reset").addEventListener("click", function () { $("#bw-model-search").value = ""; loadCatalog(true, ""); });
    $("#bw-catalog-more").addEventListener("click", function () { loadCatalog(false, state.catalog.query); });
    $("#bw-recipe-model").addEventListener("change", function () { state.recipeQuantities.clear(); renderRecipeColors(); });
    $("#bw-recipe-color").addEventListener("change", function () { state.recipeQuantities.clear(); renderRecipeComponents(); });
    $("#bw-model-edit-close").addEventListener("click", function () { state.editModelId = null; $("#bw-model-edit-card").hidden = true; });
    $("#bw-price-variant").addEventListener("change", renderPriceEdit);
    $("#bw-rule-mode").addEventListener("change", function () { const prefix = $("#bw-rule-prefix"); if ($("#bw-rule-mode").value === "numeric" && !/^\d{3}$/.test(prefix.value)) prefix.value = "200"; else if ($("#bw-rule-mode").value !== "numeric" && !/^[A-Z][A-Z0-9]{1,7}$/.test(prefix.value)) prefix.value = "BRV"; renderRuleHelp(); });
    $("#bw-receipt-model").addEventListener("change", function () { $("#bw-receipt-plan").value = ""; state.receiptExpected.clear(); state.receiptActual.clear(); state.receiptDefective.clear(); renderReceiptRecipes(); renderReceiptMatrix(); });
    $("#bw-receipt-plan").addEventListener("change", choosePlan);
    $("#bw-fill-expected").addEventListener("click", function () { try { const recipe = recipeById($("#bw-receipt-recipe").value); if (!recipe) throw new Error("Beklenen matrisi hesaplamak için bir seri reçetesi seç."); const quantity = parseQuantity($("#bw-expected-series").value, true); state.receiptExpected.clear(); recipe.components.forEach(function (component) { state.receiptExpected.set(component.variant_id, parseQuantity(component.quantity * quantity, true)); }); renderReceiptMatrix(); } catch (error) { reportError(error); } });
    $("#bw-copy-expected").addEventListener("click", function () { state.receiptActual = new Map(state.receiptExpected); renderReceiptMatrix(); setStatus("Beklenen miktarlar kopyalandı. Onaylamadan önce gerçek gelen bedenleri kontrol et.", "warning"); });
    $("#bw-stock-location").addEventListener("change", loadStock);
    $("#bw-stock-descendants").addEventListener("change", loadStock);
    $("#bw-stock-recipe").addEventListener("change", loadStock);
    $("#bw-stock-search").addEventListener("input", renderStock);
    $("#bw-stock-refresh").addEventListener("click", loadStock);
    $("#bw-stock-more").addEventListener("click", loadMoreStock);
    $("#bw-count-location").addEventListener("change", invalidateCount); $("#bw-count-model").addEventListener("change", invalidateCount);
    $("#bw-count-load").addEventListener("click", async function () { try { await loadCount(); } catch (error) { reportError(error); } });
    $("#bw-health-load").addEventListener("click", loadHealth);
    $("#bw-sale-currency").addEventListener("change", renderCart);
    $("#bw-sale-method").addEventListener("change", renderSaleTotals);
    $("#bw-sale-payment").addEventListener("input", renderSaleTotals);
    $("#bw-sale-location").addEventListener("change", function () { state.salePickId = null; renderSaleTotals(); });
    $("#bw-clear-cart").addEventListener("click", function () { if (!state.cart.length) return; state.cart = []; state.salePickId = null; renderCart(); setStatus("Sepet taslağı boşaltıldı. Stok hareketi oluşmadı."); });
    $("#bw-transfer-add").addEventListener("click", async function () { const control = $("#bw-transfer-add"); if (control.disabled) return; control.disabled = true; try { const quantity = parseQuantity($("#bw-transfer-count").value); const target = await resolveBarcode($("#bw-transfer-barcode").value); mergeCart(state.transfers, target, quantity); $("#bw-transfer-barcode").value = ""; renderTransfers(); } catch (error) { reportError(error); } finally { control.disabled = false; $("#bw-transfer-barcode").focus(); } });
    $("#bw-transfer-barcode").addEventListener("keydown", function (event) { if (event.key === "Enter") { event.preventDefault(); $("#bw-transfer-add").click(); } });
    $("#bw-picking-refresh").addEventListener("click", loadPicking);
    $("#bw-create-pick").addEventListener("click", async function () { try { const locationId = idValue("#bw-sale-location", true); const items = state.cart.map(function (item) { return { barcode: item.target.barcode, quantity: parseQuantity(item.quantity) }; }); if (!items.length) throw new Error("Önce satış ekranında bir sepet hazırla ve çıkış konumunu seç."); const location = locationById(locationId); const includeDescendants = ["store", "warehouse"].includes(location.kind) && $("#bw-picking-descendants").checked; await mutate("pick-create", "/picking/tasks", { location_id: locationId, items: items, include_descendants: includeDescendants }, async function (task) { state.activePickId = task.id; state.salePickId = task.id; await loadPicking(); renderSaleTotals(); setStatus("Toplama görevi oluşturuldu. Ürünler rezerve edildi; satış henüz yapılmadı."); }); } catch (error) { reportError(error); } });
    ["#bw-label-model", "#bw-label-kind"].forEach(function (selector) { $(selector).addEventListener("change", renderLabels); });
    ["#bw-label-size", "#bw-label-dpi", "#bw-label-note"].forEach(function (selector) { $(selector).addEventListener("change", invalidatePrint); });
    $("#bw-label-preview").addEventListener("click", async function () { try { await prepareLabels(); } catch (error) { reportError(error); } });
    $("#bw-print").addEventListener("click", function () { if (!state.printReady) { reportError(new Error("Güncel ve eksiksiz bir baskı önizlemesi hazırla.")); return; } root.print(); });
    $("#bw-return-close").addEventListener("click", function () { state.returnSale = null; $("#bw-return-card").hidden = true; });
    $("#bw-ledger-load").addEventListener("click", loadLedger);
    $("#bw-audit-load").addEventListener("click", loadAudit);
    $("#bw-account-refresh").addEventListener("click", function () { if (state.activeCustomerId) loadAccount(state.activeCustomerId); });
    root.addEventListener("hashchange", function () { safeView(root.location.hash.slice(1), false); });
    root.addEventListener("beforeunload", function (event) { if (state.cart.length || state.transfers.length || journal.current || Array.from(state.receiptActual.values()).some(function (quantity) { return Number(quantity) > 0; })) { event.preventDefault(); event.returnValue = ""; } });

    onForm("#bw-model-form", async function (form) {
      const colors = $$(".bw-color-row").map(function (row) { return { code: $("[data-color-code]", row).value.trim().toUpperCase(), name: $("[data-color-name]", row).value.trim() }; });
      if (new Set(colors.map(function (color) { return color.code; })).size !== colors.length) throw new Error("Renk kodları birbirinden farklı olmalı.");
      const prices = {}; CURRENCIES.forEach(function (currency) { const value = field(form, "price_" + currency); if (value) prices[currency] = normalizeMoney(value, true); });
      if (!Object.keys(prices).length) throw new Error("En az bir para biriminde varsayılan parça fiyatını yaz.");
      const body = { model_code: field(form, "model_code"), name: field(form, "name"), colors: colors, sizes: parseSizes(field(form, "sizes")), unit_prices: prices };
      await mutate("model", "/catalog/models", body, function (model) { state.data.models = mergeById(state.data.models, [model]); absorbMetadata({ models: [model] }); state.catalog.query = model.model_code; $("#bw-model-search").value = model.model_code; form.reset(); $("#bw-color-rows").replaceChildren(); addColorRow(); setStatus((model.name || "Model") + " oluşturuldu. Varyant barkodları hazır; stok sıfır."); }, form);
    });
    onForm("#bw-recipe-form", async function (form) {
      const components = $$("#bw-recipe-components input").map(function (input) { return { variant_id: Number(input.dataset.variantId), quantity: parseQuantity(input.value, true) }; }).filter(function (item) { return item.quantity > 0; });
      if (!components.length) throw new Error("Seriye en az bir beden ve pozitif adet ekle.");
      await mutate("recipe", "/catalog/recipes", { model_id: idValue("#bw-recipe-model", true), color_code: $("#bw-recipe-color").value, name: field(form, "name"), components: components }, function () { state.recipeQuantities.clear(); renderRecipeComponents(); setStatus("Seri reçetesi ve kalıcı seri barkodu oluşturuldu."); }, form);
    });
    onForm("#bw-model-edit-form", async function (form) { if (!state.editModelId) throw new Error("Düzenlenecek modeli katalogdan seç."); await mutate("model-edit", "/catalog/models/" + state.editModelId, { name: field(form, "name"), description: field(form, "description") }, function () { setStatus("Model bilgisi güncellendi. Mevcut barkodlar değişmedi."); }, form, "PATCH"); });
    onForm("#bw-price-edit-form", async function (form) { const prices = {}; CURRENCIES.forEach(function (currency) { const value = field(form, "price_" + currency); if (value) prices[currency] = normalizeMoney(value, true); }); if (!Object.keys(prices).length) throw new Error("En az bir para biriminde parça fiyatı kalmalı."); await mutate("price-edit", "/catalog/variants/" + idValue("#bw-price-variant", true) + "/prices", { prices: prices, reason: field(form, "reason") }, async function () { await refreshCartPrices(); setStatus("Varyant fiyatı güncellendi. Eski satışlar ve basılmış barkodlar korundu."); }, form, "PATCH"); });
    onForm("#bw-barcode-rule-form", async function (form) { const prefix = field(form, "prefix").toUpperCase(); const mode = field(form, "mode"); if (mode === "numeric" ? !/^\d{3}$/.test(prefix) : !/^[A-Z][A-Z0-9]{1,7}$/.test(prefix)) throw new Error(mode === "numeric" ? "Sayısal barkod öneki tam 3 rakam olmalı." : "Harfli önek 2–8 İngilizce harf/rakam olmalı ve harfle başlamalı."); await mutate("barcode-rule", "/barcode-rules", { mode: mode, prefix: prefix }, function () { setStatus("Barkod kuralı yalnız yeni kimlikler için kaydedildi. Mevcut ürün ve etiket kodları değişmedi."); }, form, "PUT"); });
    onForm("#bw-receipt-form", async function (form) {
      const items = receiptVariants().map(function (variant) { return { variant_id: variant.id, quantity: parseQuantity(state.receiptActual.get(variant.id) || 0, true) }; }).filter(function (item) { return item.quantity > 0; });
      const defective = receiptVariants().map(function (variant) { return { variant_id: variant.id, quantity: parseQuantity(state.receiptDefective.get(variant.id) || 0, true) }; }).filter(function (item) { return item.quantity > 0; });
      if (!items.length && !defective.length) throw new Error("En az bir bedene gerçek gelen miktarı yaz.");
      const body = { location_id: idValue("#bw-receipt-location", true), reference: field(form, "reference"), items: items };
      if ($("#bw-receipt-plan").value) body.plan_id = idValue("#bw-receipt-plan", true);
      if (defective.length) { body.defective = defective; body.quarantine_location_id = idValue("#bw-receipt-quarantine", true); }
      await mutate("receipt", "/receipts", body, function (receipt, replayed) { state.receiptActual.clear(); state.receiptExpected.clear(); state.receiptDefective.clear(); form.elements.reference.value = ""; $("#bw-expected-series").value = "0"; renderReceiptMatrix(); setStatus((replayed ? "Önceden kaydedilen" : "Yeni") + " mal kabul doğrulandı. Aynı işlem ikinci stok artışı yapmaz."); }, form);
    });
    onForm("#bw-plan-form", async function (form) {
      const items = receiptVariants().map(function (variant) { return { variant_id: variant.id, quantity: parseQuantity(state.receiptExpected.get(variant.id) || 0, true) }; }).filter(function (item) { return item.quantity > 0; });
      if (!items.length) throw new Error("Önce mal kabul matrisindeki beklenen beden miktarlarını hazırla.");
      await mutate("plan", "/production/plans", { reference: field(form, "reference"), workshop: field(form, "workshop"), items: items }, function () { form.reset(); setStatus("Atölye üretim planı kaydedildi. Fiziksel stok değişmedi."); }, form);
    });
    onForm("#bw-package-form", async function (form) { await mutate("package", "/packages", { location_id: idValue("#bw-package-location", true), recipe_id: idValue("#bw-package-recipe", true) }, function (item) { setStatus("Fiziksel paket oluşturuldu: " + item.barcode + ". Parçalar çift sayılmadan pakete ayrıldı."); }, form); });
    onForm("#bw-count-form", async function (form) {
      const snapshot = state.countSnapshot; if (!snapshot) throw new Error("Önce sayılacak konumun güncel miktarlarını getir.");
      if (snapshot.location_id !== idValue("#bw-count-location", true) || snapshot.model_id !== idValue("#bw-count-model", true)) throw new Error("Konum veya model değişti. Sayım tablosunu yenile.");
      const items = snapshot.rows.map(function (row) { const input = $('[data-count-variant="' + row.variant.id + '"]'); return { variant_id: row.variant.id, expected_quantity: row.expected_quantity, counted_quantity: parseQuantity(input.value, true) }; });
      await mutate("stock-count", "/stock-counts", { location_id: snapshot.location_id, reference: field(form, "reference"), reason: field(form, "reason"), items: items }, function () { invalidateCount(); form.elements.reference.value = ""; form.elements.reason.value = ""; setStatus("Fiziksel sayım farkları denetim kaydıyla işlendi. Eski hareketler korundu."); }, form);
    });
    onForm("#bw-location-form", async function (form) { await mutate("location", "/locations", { code: field(form, "code"), name: field(form, "name"), kind: field(form, "kind"), parent_id: idValue("#bw-location-parent", false) }, function () { form.reset(); setStatus("Stok konumu oluşturuldu."); }, form); });
    onForm("#bw-transfer-form", async function (form) { const from = idValue("#bw-transfer-from", true); const to = idValue("#bw-transfer-to", true); if (from === to) throw new Error("Çıkış ve varış konumları farklı olmalı."); await mutate("transfer", "/transfers", { from_location_id: from, to_location_id: to, reference: field(form, "reference"), items: expandTransfer(state.transfers) }, function () { state.transfers = []; renderTransfers(); form.elements.reference.value = ""; setStatus("Transfer tamamlandı. Toplam fiziksel parça sayısı değişmedi."); }, form); });
    onForm("#bw-scan-form", async function () {
      if (cartLocked()) throw new Error("Bekleyen satış veya rezervasyon işlemi sonuçlanmadan sepet değiştirilemez.");
      const input = $("#bw-sale-barcode"); const code = input.value.trim(); const quantity = parseQuantity($("#bw-sale-count").value);
      if (!code) throw new Error("Bir barkod okut veya numarayı yaz.");
      input.value = ""; $("#bw-sale-count").value = "1"; input.focus();
      saleScanQueue.enqueue({ code: code, quantity: quantity }).catch(reportError);
    });
    onForm("#bw-sale-form", async function (form) {
      if (state.scanBusy) throw new Error("Okutma kuyruğu tamamlanana kadar bekle.");
      const currency = $("#bw-sale-currency").value; const items = buildSaleItems(state.cart, currency); const summary = cartTotal(currency); const method = $("#bw-sale-method").value; const amount = method === "credit" ? "0.00" : $("#bw-sale-payment").value.trim() ? normalizeMoney($("#bw-sale-payment").value, true) : moneyString(summary.minor);
      const customer = idValue("#bw-sale-customer", false); if ((method === "credit" || moneyMinor(amount) < summary.minor) && !customer) throw new Error("Veresiye veya kısmi ödemede müşteri seçmelisin."); if (moneyMinor(amount) > summary.minor) throw new Error("Alınan tutar sepet toplamını aşamaz. Para üstünü düşüp net tutarı yaz.");
      const body = { location_id: idValue("#bw-sale-location", true), customer_id: customer, currency: currency, expected_total: moneyString(summary.minor), items: items, payment: { amount: amount, method: method } }; if (state.salePickId) body.picking_task_id = state.salePickId;
      await mutate("sale", "/sales", body, function (sale, replayed) { state.cart = []; state.salePickId = null; $("#bw-sale-payment").value = ""; renderCart(); const success = $("#bw-sale-success"); success.hidden = false; success.className = "bw-sale-success"; success.replaceChildren(node("h3", "", replayed ? "Satış kaydı doğrulandı" : "Satış tamamlandı"), node("p", "", (sale.reference || "Satış #" + sale.id) + " · " + formatMoney(sale.total, sale.currency)), node("p", "", "Alınan: " + formatMoney(sale.paid, sale.currency) + " · Cariye kalan: " + formatMoney(sale.balance_due, sale.currency)), saleDocumentLink(sale.id)); setStatus("Satış, stok ve ödeme/cari aynı işlemde kaydedildi."); }, form);
    });
    onForm("#bw-customer-form", async function (form) { const body = { name: field(form, "name") }; if (field(form, "phone")) body.phone = field(form, "phone"); await mutate("customer", "/customers", body, function () { form.reset(); setStatus("Müşteri kaydedildi."); }, form); });
    onForm("#bw-payment-form", async function (form) { await mutate("payment", "/payments", { customer_id: idValue("#bw-payment-customer", true), amount: normalizeMoney(field(form, "amount"), false), currency: field(form, "currency"), direction: field(form, "direction"), method: field(form, "method"), reference: field(form, "reference") }, function () { form.elements.amount.value = ""; form.elements.reference.value = ""; setStatus("Cari hareketi kaydedildi. Güncel bakiye sunucudan alınıyor."); }, form); });
    onForm("#bw-return-form", async function (form) { if (!state.returnSale) throw new Error("Önce iade alınacak satışı seç."); const items = $$("[data-sale-item-id]", form).map(function (input) { const quantity = parseQuantity(input.value, true); if (quantity > Number(input.max)) throw new Error("İade adedi satılan ve henüz iade edilmemiş miktarı aşamaz."); return { sale_item_id: Number(input.dataset.saleItemId), quantity: quantity }; }).filter(function (item) { return item.quantity > 0; }); if (!items.length) throw new Error("İade alınacak bedenlerin miktarını yaz."); const body = { location_id: idValue("#bw-return-location", true), items: items, reason: field(form, "reason"), refund_amount: field(form, "refund_amount") ? normalizeMoney(field(form, "refund_amount"), true) : "0.00", refund_method: field(form, "refund_method") }; await mutate("return", "/sales/" + state.returnSale.id + "/returns", body, function () { state.returnSale = null; form.reset(); $("#bw-return-card").hidden = true; invalidateSaleDetail(); setStatus("Satışa bağlı iade ve cari karşılığı kaydedildi."); }, form); });
  }

  function start() {
    $("#bw-date").textContent = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "long", year: "numeric", timeZone: "Europe/Istanbul" }).format(new Date());
    saleScanQueue = new SerialScanQueue(async function (event) { const target = await resolveBarcode(event.code); mergeCart(state.cart, target, event.quantity); state.salePickId = null; const result = $("#bw-scan-result"); result.dataset.visible = "true"; result.textContent = targetTitle(target) + " · " + targetDescription(target) + " · " + event.quantity + " eklendi"; $("#bw-sale-success").hidden = true; renderCart(); return target; }, function (busy, queued) { state.scanBusy = busy; $("#bw-scan-form").setAttribute("aria-busy", String(busy)); $('#bw-scan-form button[type="submit"]').textContent = queued > 0 ? "Ekle · " + queued + " sırada" : "Ekle ↵"; renderSaleTotals(); applyInteractionLocks(); });
    addColorRow(); wireEvents(); renderTransfers(); renderCart(); applyPermissions(); safeView(root.location.hash.slice(1) || "overview", false); loadData(false);
  }
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", start, { once: true }); else start();
})(typeof window === "undefined" ? null : window);
