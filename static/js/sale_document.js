(() => {
  'use strict';
  document.getElementById('print-document')?.addEventListener('click', () => window.print());
  try {
    const formatter = new Intl.DateTimeFormat('tr-TR', {
      timeZone: 'Europe/Istanbul', dateStyle: 'medium', timeStyle: 'short'
    });
    document.querySelectorAll('time[datetime]').forEach(element => {
      const value = element.getAttribute('datetime');
      // The immutable UTC timestamp stays in the attribute. An invalid or
      // unsupported value leaves the server-rendered UTC fallback readable.
      if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return;
      const timestamp = new Date(value);
      if (!Number.isFinite(timestamp.getTime())) return;
      element.textContent = formatter.format(timestamp) + ' (İstanbul)';
    });
  } catch {
    // A browser without this locale/timezone keeps the explicit UTC fallback.
  }
})();
