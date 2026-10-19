/* Private product photos; bounded local preparation and a durable retry key. */
(function (root) {
  'use strict';
  const MAX_BYTES = 350000;
  function validatePending(record, actor) {
    if (!record || record.version !== 1 || record.actor !== actor.id || record.company !== actor.company ||
        !Number.isSafeInteger(record.model_id) || record.model_id < 1 ||
        typeof record.key !== 'string' || !/^[A-Za-z0-9_.:-]{8,100}$/.test(record.key) ||
        typeof record.image_base64 !== 'string' || !record.image_base64 || record.image_base64.length > 466668 ||
        !/^[A-Za-z0-9+/]+={0,2}$/.test(record.image_base64)) {
      throw new Error('Önceki fotoğraf taslağı doğrulanamadı. Yeni yükleme açılmadı.');
    }
    return record;
  }
  class PhotoJournal {
    constructor(storage, actor) {
      this.storage = storage; this.actor = Object.freeze({ id: actor.id, company: actor.company });
      this.name = 'briva.photo.v1.' + actor.id + '.' + encodeURIComponent(actor.company);
      this.pending = null;
    }
    restore() {
      const value = this.storage.getItem(this.name);
      if (!value) return null;
      const saved = JSON.parse(value);
      if (['complete', 'rejected'].includes(saved.status) && saved.actor === this.actor.id && saved.company === this.actor.company) return null;
      this.pending = validatePending(saved, this.actor);
      return this.pending;
    }
    prepare(modelId, base64, key, modelName) {
      if (this.pending) throw new Error('Önceki yüklemenin sonucunu kontrol edin.');
      const value = validatePending({ version: 1, actor: this.actor.id, company: this.actor.company,
        model_id: modelId, key, image_base64: base64, model_name: String(modelName).slice(0, 220), status: 'pending' }, this.actor);
      // Persist before the first dispatch. Every later retry uses identical bytes.
      this.storage.setItem(this.name, JSON.stringify(value));
      this.pending = value;
      return value;
    }
    complete(result) {
      if (!this.pending || !Number.isSafeInteger(result.id) || result.model_id !== this.pending.model_id) {
        throw new Error('Fotoğraf yüklemesinin sonucu doğrulanamadı. Aynı yüklemeyi tekrar kontrol edin.');
      }
      this.storage.setItem(this.name, JSON.stringify({ version: 1, status: 'complete',
        actor: this.actor.id, company: this.actor.company, key: this.pending.key,
        photo_id: result.id, model_id: result.model_id }));
      this.pending = null;
    }
    reject(error) {
      // These two domain codes are produced after the idempotency lookup.
      // A committed upload would replay successfully before either check.
      if (!this.pending || !((error.code === 'invalid_image' && error.status === 400) ||
          (error.code === 'photo_limit' && error.status === 409))) return false;
      this.storage.setItem(this.name, JSON.stringify({ version: 1, status: 'rejected',
        actor: this.actor.id, company: this.actor.company, key: this.pending.key,
        model_id: this.pending.model_id, code: error.code }));
      this.pending = null;
      return true;
    }
  }
  const exported = { PhotoJournal, validatePending, MAX_BYTES };
  if (typeof module !== 'undefined' && module.exports) module.exports = exported;
  if (!root.document) return;
  const $ = selector => document.querySelector(selector);
  const state = { actor: null, permissions: {}, models: new Map(), selected: null, next: null,
    query: '', busy: false, prepared: null, journal: null, storageReady: false, preview: null };
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  };
  function message(text, error = false) {
    const node = $('#photo-message'); node.textContent = text; node.dataset.error = String(error); node.hidden = false;
  }
  function controls() {
    const pending = state.journal?.pending;
    document.querySelectorAll('button, input').forEach(node => { node.disabled = state.busy; });
    $('#photo-save').disabled = state.busy || !state.prepared || !state.storageReady || !!pending;
    $('#photo-file').disabled = state.busy || !!pending || !state.storageReady;
    $('#photo-pending').hidden = !pending;
    if (pending) $('#photo-pending-description').textContent = pending.model_name + ' modeline ait fotoğraf aynı işlem anahtarıyla tekrar kontrol edilir. Yeni fotoğraf eklemeden önce bunu tamamlayın.';
  }
  async function act(operation) {
    if (state.busy) return;
    state.busy = true; controls();
    try { await operation(); }
    catch (error) { message(error.message || 'İşlem tamamlanamadı.', true); }
    finally { state.busy = false; controls(); }
  }
  async function getJSON(path, options) {
    const response = await fetch(path, options);
    let body;
    try { body = await response.json(); }
    catch { throw new Error('Sunucudan doğrulanabilir yanıt alınamadı. Yüklemeyi aynı anahtarla tekrar kontrol edin.'); }
    if (!response.ok) {
      const error = new Error(body.error?.message || body.message || (response.status === 401 ? 'Oturumunuz kapanmış. Giriş yapıp bu sekmeye dönün.' : 'İşlem tamamlanamadı.'));
      error.code = body.error?.code; error.status = response.status; throw error;
    }
    return body.data;
  }
  function showModels() {
    const target = $('#photo-models'); target.replaceChildren();
    for (const model of state.models.values()) {
      const button = element('button', undefined, 'model'); button.type = 'button';
      button.setAttribute('aria-pressed', String(state.selected?.id === model.id));
      button.append(element('strong', model.model_code), element('span', model.name));
      button.addEventListener('click', () => act(() => selectModel(model)));
      target.append(button);
    }
    if (!state.models.size) target.append(element('p', 'Bu aramada model bulunamadı.', 'hint'));
    $('#photo-more').hidden = state.next === null;
    $('#photo-count').textContent = state.models.size + ' model gösteriliyor.';
  }
  async function loadModels(more = false) {
    const parameters = new URLSearchParams({ q: state.query, limit: '30' });
    if (more && state.next !== null) parameters.set('after_model_id', String(state.next));
    const data = await getJSON('/api/v2/catalog?' + parameters);
    if (!more) state.models.clear();
    for (const model of data.models) state.models.set(model.id, model);
    state.next = data.pagination.has_more ? data.pagination.next_after_model_id : null;
    showModels();
  }
  function clearPreview(resetInput = true) {
    state.prepared = null;
    if (state.preview) URL.revokeObjectURL(state.preview);
    state.preview = null;
    $('#photo-preview').removeAttribute('src');
    $('#photo-preview-wrap').hidden = true;
    if (resetInput) $('#photo-file').value = '';
  }
  async function selectModel(model) {
    state.selected = model; clearPreview();
    $('#photo-gallery').replaceChildren(element('p', 'Fotoğraflar yükleniyor…', 'hint'));
    $('#photo-history-count').hidden = true;
    $('#photo-model-title').textContent = model.model_code + ' · ' + model.name;
    $('#photo-empty').hidden = true;
    $('#photo-upload').hidden = !state.permissions.catalog_edit;
    $('#photo-readonly').hidden = !!state.permissions.catalog_edit;
    showModels();
    await loadGallery();
  }
  async function loadGallery() {
    const data = await getJSON('/api/v2/media/models/' + state.selected.id + '/images');
    const gallery = $('#photo-gallery'); gallery.replaceChildren();
    $('#photo-history-count').hidden = false;
    $('#photo-history-count').textContent = data.items.length + ' / 30 fotoğraf';
    if (!data.items.length) gallery.append(element('p', 'Bu modelin henüz fotoğrafı yok.', 'hint'));
    for (const [index, photo] of data.items.entries()) {
      if (!/^\/api\/v2\/media\/images\/[1-9][0-9]*$/.test(photo.url)) throw new Error('Fotoğraf adresi doğrulanamadı.');
      const figure = element('figure'); const link = element('a'); link.href = photo.url; link.target = '_blank'; link.rel = 'noopener';
      const image = element('img'); image.src = photo.url; image.alt = state.selected.model_code + ' ürün fotoğrafı ' + (index + 1); image.loading = 'lazy';
      image.width = photo.width; image.height = photo.height; link.append(image);
      const date = new Intl.DateTimeFormat('tr-TR', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Europe/Istanbul' }).format(new Date(photo.created_at));
      figure.append(link, element('figcaption', (index === 0 ? 'En yeni · ' : '') + date)); gallery.append(figure);
    }
  }
  const toBlob = (canvas, quality) => new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('Fotoğraf hazırlanamadı.')), 'image/jpeg', quality));
  async function prepareFile(file) {
    if (!file || !['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size < 1 || file.size > 15000000) {
      throw new Error('En fazla 15 MB boyutunda JPEG, PNG veya WebP seçin.');
    }
    const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
    try {
      if (bitmap.width * bitmap.height > 24000000) throw new Error('Fotoğraf en fazla 24 megapiksel olmalı.');
      const scale = Math.min(1, 1280 / Math.max(bitmap.width, bitmap.height));
      const canvas = document.createElement('canvas'); canvas.width = Math.max(1, Math.round(bitmap.width * scale)); canvas.height = Math.max(1, Math.round(bitmap.height * scale));
      const context = canvas.getContext('2d');
      if (!context) throw new Error('Tarayıcı fotoğraf hazırlamayı desteklemiyor.');
      context.fillStyle = '#ffffff'; context.fillRect(0, 0, canvas.width, canvas.height); context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      let blob;
      for (const quality of [0.85, 0.75, 0.6, 0.45]) { blob = await toBlob(canvas, quality); if (blob.size <= MAX_BYTES) break; }
      if (blob.size > MAX_BYTES) throw new Error('Fotoğraf küçültülse de çok büyük. Daha küçük bir fotoğraf seçin.');
      const base64 = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = () => reject(new Error('Fotoğraf okunamadı.')); reader.readAsDataURL(blob); });
      return { blob, base64, width: canvas.width, height: canvas.height };
    } finally { bitmap.close(); }
  }
  async function sendPending() {
    const pending = state.journal?.pending;
    if (!pending) throw new Error('Bekleyen fotoğraf yok.');
    let result;
    try {
      result = await getJSON('/api/v2/media/models/' + pending.model_id + '/images', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': pending.key,
          'X-Briva-Actor': String(pending.actor), 'X-Briva-Company': encodeURIComponent(pending.company) },
        body: JSON.stringify({ image_base64: pending.image_base64 })
      });
    } catch (error) { state.journal.reject(error); throw error; }
    state.journal.complete(result); clearPreview(); message('Fotoğraf modele eklendi. Ürün ve stok kayıtları korundu.');
    if (state.selected?.id === result.model_id) await loadGallery();
  }
  $('#photo-search').addEventListener('submit', event => { event.preventDefault(); act(async () => { state.query = $('#photo-query').value.trim(); await loadModels(); }); });
  $('#photo-more').addEventListener('click', () => act(() => loadModels(true)));
  $('#photo-file').addEventListener('change', event => {
    const file = event.target.files[0];
    act(async () => {
      clearPreview(false); if (!file) return;
      const prepared = await prepareFile(file); state.prepared = prepared;
      state.preview = URL.createObjectURL(prepared.blob); $('#photo-preview').src = state.preview;
      $('#photo-preview-wrap').hidden = false;
      $('#photo-file-summary').textContent = prepared.width + ' × ' + prepared.height + ' px · ' + Math.ceil(prepared.blob.size / 1000) + ' KB';
    });
  });
  $('#photo-upload').addEventListener('submit', event => {
    event.preventDefault(); act(async () => {
      if (!state.selected || !state.prepared || !state.storageReady) throw new Error('Önce model ve fotoğraf seçin.');
      if (!root.crypto?.randomUUID) throw new Error('Tarayıcı güvenli işlem anahtarı üretemiyor. Güncel tarayıcı kullanın.');
      state.journal.prepare(state.selected.id, state.prepared.base64, root.crypto.randomUUID(), state.selected.model_code + ' · ' + state.selected.name);
      controls(); await sendPending();
    });
  });
  $('#photo-retry').addEventListener('click', () => act(sendPending));
  root.addEventListener('beforeunload', event => {
    if (state.busy || state.prepared || state.journal?.pending) { event.preventDefault(); event.returnValue = ''; }
  });
  act(async () => {
    const data = await getJSON('/api/v2/bootstrap');
    state.actor = Object.freeze({ id: data.user.id, company: data.company }); state.permissions = data.permissions;
    $('#photo-actor').textContent = data.user.username + ' · ' + data.company;
    try { state.journal = new PhotoJournal(root.sessionStorage, state.actor); state.journal.restore(); state.storageReady = true; }
    catch { message('Tarayıcı fotoğraf yükleme taslağını koruyamıyor. Yükleme kapalı; mevcut fotoğrafları görebilirsiniz.', true); }
    await loadModels();
    const modelId = new URLSearchParams(location.search).get('model');
    if (modelId && /^[1-9][0-9]{0,14}$/.test(modelId)) {
      const selected = await getJSON('/api/v2/catalog?model_id=' + modelId);
      if (selected.models.length) { state.models.set(selected.models[0].id, selected.models[0]); await selectModel(selected.models[0]); }
    }
  });
})(typeof window !== 'undefined' ? window : globalThis);
