/* Shared text/URL helpers. escapeHtml is for text and quoted HTML attributes;
   it is deliberately not an HTML sanitizer or a JavaScript-string encoder.
   Never interpolate untrusted data into event handlers, CSS or script source. */
(() => {
  'use strict';
  const string = value => value == null ? '' : String(value);
  const escapeHtml = value => string(value).replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[character]));
  const finiteNumber = (value, fallback = 0) => {
    if (typeof value === 'boolean' || value === null || value === '') return fallback;
    const result = Number(value);
    return Number.isFinite(result) ? result : fallback;
  };
  const text = (element, value) => {
    element.textContent = string(value);
    return element;
  };
  const appendText = (element, value) => {
    element.appendChild(document.createTextNode(string(value)));
    return element;
  };
  const sameOriginURL = value => {
    try {
      const target = new URL(string(value), location.href);
      if (!['http:', 'https:'].includes(target.protocol) || target.origin !== location.origin) return '';
      if (target.username || target.password) return '';
      return target.pathname + target.search + target.hash;
    } catch { return ''; }
  };
  window.BrivaDOM = Object.freeze({ escapeHtml, finiteNumber, text, appendText, sameOriginURL });
})();
