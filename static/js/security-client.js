/* Shared CSRF transport for retained legacy pages. No cross-origin token leaks. */
(() => {
  'use strict';
  const safe = new Set(['GET', 'HEAD', 'OPTIONS']);
  const token = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
  const setToken = value => {
    if (!value) return;
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) meta.content = value;
    document.querySelectorAll('input[name="csrf_token"]').forEach(input => { input.value = value; });
  };
  const local = value => {
    try { return new URL(value, location.href).origin === location.origin; }
    catch { return false; }
  };
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (resource, options) => {
    const url = resource instanceof Request ? resource.url : String(resource);
    const method = String(options?.method || (resource instanceof Request ? resource.method : 'GET')).toUpperCase();
    let init = options;
    if (local(url) && !safe.has(method)) {
      const headers = new Headers(options?.headers || (resource instanceof Request ? resource.headers : undefined));
      headers.set('X-CSRF-Token', token());
      init = { ...options, headers };
    }
    const response = await nativeFetch(resource, init);
    if (local(url)) setToken(response.headers.get('X-CSRF-Token'));
    return response;
  };
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url, ...rest) {
    this.brivaCsrfRequired = local(String(url)) && !safe.has(String(method).toUpperCase());
    return originalOpen.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function(body) {
    if (this.brivaCsrfRequired) this.setRequestHeader('X-CSRF-Token', token());
    this.addEventListener('load', () => {
      if (this.brivaCsrfRequired) setToken(this.getResponseHeader('X-CSRF-Token'));
    }, { once: true });
    return originalSend.call(this, body);
  };
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('form').forEach(form => {
      if (safe.has(form.method.toUpperCase()) || !local(form.action)) return;
      let input = form.querySelector('input[name="csrf_token"]');
      if (!input) {
        input = document.createElement('input');
        input.type = 'hidden'; input.name = 'csrf_token'; form.appendChild(input);
      }
      input.value = token();
    });
  });
})();
