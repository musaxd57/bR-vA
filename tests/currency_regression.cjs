// Runs the application's actual currency callback and addToCartAction with a
// synthetic DOM. No browser, server, database, network, or extra packages needed.
// Run: node tests/currency_regression.cjs
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { test } = require('node:test');

const source = fs.readFileSync(
  path.join(__dirname, '../static/js/storage.js'), 'utf8',
);

function extractSection(startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  assert.notEqual(start, -1, `Source section is missing: ${startMarker}`);
  const end = source.indexOf(endMarker, start);
  assert.notEqual(end, -1, `Source section end is missing: ${endMarker}`);
  return source.slice(start, end);
}

const currencyHandler = extractSection(
  '  currencyButtons.forEach(btn => {',
  '  const globalDiscountInput =',
);
const addToCart = extractSection(
  '    function addToCartAction() {',
  '  stokTbody.addEventListener("click", async (e) => {',
);

function createHarness() {
  const display = { textContent: '' };
  const buttons = ['TL', 'USD', 'EURO'].map(currency => {
    const classes = new Set();
    const handlers = new Map();
    return {
      dataset: { currency },
      classList: {
        add(name) { classes.add(name); },
        remove(name) { classes.delete(name); },
        contains(name) { return classes.has(name); },
      },
      addEventListener(event, handler) { handlers.set(event, handler); },
      click() { handlers.get('click')(); },
    };
  });
  const prices = { TL: 1000, USD: 25, EURO: 30 };
  const context = {
    activeSaleCurrency: 'USD',
    currencyButtons: buttons,
    shoppingCart: [],
    selectedCurrencyInput: { value: 'USD' },
    selectedPriceInput: { value: '' },
    productList: {
      DEMO: {
        code: 'DEMO', name: 'Synthetic shirt', currency: prices,
        sizes: [{ value: 'M', number: 1 }],
      },
    },
    satisKodInput: { value: 'DEMO' },
    kodInputSorgu: { value: 'DEMO', focus() {} },
    isSingleSaleMode: false,
    yuzdelikIndirimInput: null,
    renderCart() {},
    showToast() {},
    document: {
      getElementById(id) {
        return id === 'selected-price-display' ? display : null;
      },
    },
  };
  vm.createContext(context);
  vm.runInContext(currencyHandler + '\n' + addToCart, context, {
    filename: 'storage.js: currency callback and addToCartAction',
    timeout: 1000,
  });
  return { context, display, prices, buttons };
}

for (const currency of ['TL', 'USD', 'EURO']) {
  test(`${currency}: displayed price and new cart line use the selected currency`, () => {
    const { context, display, prices, buttons } = createHarness();
    // Start in a different currency to exercise a real change for every case.
    context.activeSaleCurrency = currency === 'USD' ? 'TL' : 'USD';
    buttons.find(button => button.dataset.currency === currency).click();
    vm.runInContext('addToCartAction()', context, { timeout: 1000 });

    assert.equal(context.activeSaleCurrency, currency);
    assert.equal(context.selectedCurrencyInput.value, currency);
    assert.equal(Number(context.selectedPriceInput.value), prices[currency]);
    assert.equal(display.textContent,
      `Seçilen Fiyat: ${prices[currency].toFixed(2)} ${currency}`);
    assert.deepEqual(
      buttons.filter(button => button.classList.contains('active'))
        .map(button => button.dataset.currency),
      [currency],
    );
    assert.equal(context.shoppingCart.length, 1);
    assert.equal(context.shoppingCart[0].currency, currency);
    assert.equal(context.shoppingCart[0].price, prices[currency]);
    assert.equal(context.shoppingCart[0].quantity, 1);
  });
}

test('repeated currency changes keep existing and newly added series consistent', () => {
  const { context, prices, buttons } = createHarness();
  let expectedQuantity = 0;
  for (const currency of ['TL', 'USD', 'EURO', 'TL']) {
    buttons.find(button => button.dataset.currency === currency).click();
    vm.runInContext('addToCartAction()', context, { timeout: 1000 });
    expectedQuantity += 1;

    assert.equal(context.shoppingCart.length, 1);
    assert.equal(context.shoppingCart[0].currency, currency);
    assert.equal(context.shoppingCart[0].price, prices[currency]);
    assert.equal(context.shoppingCart[0].quantity, expectedQuantity);
  }
});
