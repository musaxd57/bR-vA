'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const filename = path.join(__dirname, '../static/js/product_photos.js');
const loaded = new Module(filename, module);
loaded.filename = filename;
loaded._compile(fs.readFileSync(filename, 'utf8'), filename);
const { PhotoJournal } = loaded.exports;
const actor = { id: 7, company: 'Türkçe Mağaza' };
const storage = () => { const records = new Map(); return { getItem: key => records.get(key) || null, setItem: (key, value) => records.set(key, value) }; };
test('a lost photo response restores identical bytes, model, principal and retry key', () => {
  const tab = storage(); const first = new PhotoJournal(tab, actor);
  first.prepare(19, 'YWJj', 'test-photo-request-123', 'OX-001');
  const next = new PhotoJournal(tab, actor); const recovered = next.restore();
  assert.deepEqual(recovered, first.pending);
  assert.equal(recovered.image_base64, 'YWJj');
  assert.throws(() => next.prepare(20, 'YWJj', 'another-key-123', 'OX-002'));
  // Unauthorized, CSRF and transport errors do not call complete: state stays.
  assert.equal(new PhotoJournal(tab, actor).restore().key, 'test-photo-request-123');
});
test('a different account or company cannot adopt a pending photo', () => {
  const tab = storage(); new PhotoJournal(tab, actor).prepare(19, 'YWJj', 'test-photo-request-123', 'OX-001');
  assert.equal(new PhotoJournal(tab, { id: 8, company: actor.company }).restore(), null);
  assert.equal(new PhotoJournal(tab, { id: 7, company: 'Başka' }).restore(), null);
});
test('storage denial prevents creating an untracked upload', () => {
  const journal = new PhotoJournal({ setItem() { throw new Error('QuotaExceeded'); } }, actor);
  assert.throws(() => journal.prepare(19, 'YWJj', 'test-photo-request-123', 'OX-001'));
  assert.equal(journal.pending, null);
});
test('only a matching successful photo result retires the payload', () => {
  const tab = storage(); const journal = new PhotoJournal(tab, actor);
  journal.prepare(19, 'YWJj', 'test-photo-request-123', 'OX-001');
  assert.throws(() => journal.complete({ id: 2, model_id: 20 }));
  assert.ok(journal.pending);
  journal.complete({ id: 2, model_id: 19 });
  assert.equal(journal.pending, null);
  assert.equal(new PhotoJournal(tab, actor).restore(), null);
  assert.ok(!tab.getItem(journal.name).includes('YWJj'));
});
test('corrupt saved photo is rejected without replacing or dispatching it', () => {
  const tab = storage(); const journal = new PhotoJournal(tab, actor);
  tab.setItem(journal.name, '{bad-json');
  assert.throws(() => journal.restore());
  assert.equal(tab.getItem(journal.name), '{bad-json');
});
test('only definitive domain rejection releases a pending photo, auth and ambiguous errors preserve it', () => {
  const tab = storage(); const journal = new PhotoJournal(tab, actor);
  journal.prepare(19, 'YWJj', 'test-photo-request-123', 'OX-001');
  for (const error of [{ status: 401 }, { status: 403 }, { code: 'auth_context_changed', status: 409 },
    { code: 'operation_failed', status: 500 }, { code: 'database_busy', status: 503 }, new Error('Lost response')]) {
    assert.equal(journal.reject(error), false); assert.ok(journal.pending);
  }
  assert.equal(journal.reject({ code: 'photo_limit', status: 409 }), true);
  assert.equal(new PhotoJournal(tab, actor).restore(), null);
  journal.prepare(19, 'YWJj', 'new-photo-request-123', 'OX-001');
  assert.equal(journal.reject({ code: 'invalid_image', status: 400 }), true);
  assert.equal(new PhotoJournal(tab, actor).restore(), null);
});
