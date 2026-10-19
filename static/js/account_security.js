/* Account security: secrets live only in the current DOM until dismissed.
   No local/session storage, external QR service, HTML rendering or logging. */
(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const mode = document.body.dataset.securityMode;
  let busy = false;
  let actorContext = null;
  const message = (text, error = false) => {
    const target = $('#as-message');
    target.textContent = text; target.dataset.error = String(error); target.hidden = false;
  };
  const setBusy = value => {
    busy = value;
    document.querySelectorAll('button').forEach(button => { button.disabled = value; });
  };
  const requestJSON = async (path, payload) => {
    const options = payload === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) };
    if (payload !== undefined && mode !== 'challenge') {
      if (actorContext === null) throw new Error('Önce hesap bilgileri yüklenmeli. Sayfayı yenileyin.');
      options.headers['X-Briva-Actor'] = String(actorContext.id);
      options.headers['X-Briva-Company'] = encodeURIComponent(actorContext.company);
    }
    const response = await fetch(path, options);
    let body;
    try { body = await response.json(); }
    catch { throw new Error('Sunucudan doğrulanabilir bir yanıt alınamadı. Durumu yenileyip kontrol edin.'); }
    if (!response.ok) {
      if (response.status === 401 && mode !== 'challenge' && body.error?.code === 'authentication_required') location.assign('/login');
      throw new Error(body.error?.message || body.message || 'İşlem tamamlanamadı.');
    }
    return body.data;
  };
  const act = async function (operation) {
    if (busy) return;
    setBusy(true);
    try { await operation(); }
    catch (error) { message(error.message || 'İşlem tamamlanamadı.', true); }
    finally { setBusy(false); }
  };
  const clearPasswords = () => document.querySelectorAll('input[type="password"]').forEach(input => { input.value = ''; });
  const copy = async value => {
    if (!navigator.clipboard) throw new Error('Tarayıcı kopyalamaya izin vermedi. Metni seçip elle kopyalayabilirsiniz.');
    await navigator.clipboard.writeText(value);
    message('Kopyalandı. Güvenli bir yerde saklayın.');
  };
  const showRecovery = codes => {
    const text = $('#as-recovery-codes');
    text.textContent = codes.join('\n');
    $('#as-recovery-section').hidden = false;
    $('#as-recovery-section').scrollIntoView({ behavior: 'smooth', block: 'center' });
  };
  const date = seconds => new Intl.DateTimeFormat('tr-TR', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Europe/Istanbul' }).format(new Date(seconds * 1000));
  const node = (tag, text, className) => {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = String(text);
    if (className) result.className = className;
    return result;
  };
  const sessionPassword = () => {
    const input = $('#as-sessions-password');
    if (!input.reportValidity()) return null;
    return input.value;
  };
  const loadStatus = async () => {
    const state = await requestJSON('/api/account-security');
    if (actorContext !== null && (actorContext.id !== state.actor.id || actorContext.company !== state.actor.company)) {
      throw new Error('Bu sekme başka bir hesaba ait. Sayfayı yenileyin.');
    }
    actorContext = Object.freeze({ id: state.actor.id, company: state.actor.company });
    $('#as-account-name').textContent = state.actor.username + ' · ' + state.actor.company;
    $('#as-mfa-badge').textContent = state.mfa_enabled ? 'İki aşamalı doğrulama açık' : 'İki aşamalı doğrulama kapalı';
    $('#as-mfa-badge').dataset.enabled = String(state.mfa_enabled);
    $('#as-mfa-description').textContent = state.mfa_enabled ? 'Girişte şifrenizden sonra doğrulayıcı uygulamanızın kodu istenir.' : 'İsterseniz şifrenizin yanına telefonunuzdaki doğrulayıcı uygulamanın kodunu ekleyebilirsiniz.';
    $('#as-owner-only').hidden = state.can_manage_mfa;
    $('#as-mfa-unavailable').hidden = !state.can_manage_mfa || state.mfa_configured;
    $('#as-setup-form').hidden = !state.can_manage_mfa || state.mfa_enabled || !state.mfa_configured;
    $('#as-factor-actions').hidden = !state.can_manage_mfa || !state.mfa_enabled;
    $('#as-recovery-count').hidden = !state.mfa_enabled;
    $('#as-recovery-count').textContent = state.recovery_codes_remaining + ' kullanılmamış kurtarma kodu kaldı.';
    const list = $('#as-sessions');
    list.replaceChildren();
    for (const entry of state.sessions) {
      const row = node('div', undefined, 'as-session');
      const description = node('div');
      description.appendChild(node('strong', entry.device));
      if (entry.is_current) description.appendChild(node('span', 'Bu oturum', 'as-badge'));
      description.appendChild(node('p', 'Açılış: ' + date(entry.created_at) + ' · Bitiş: ' + date(entry.expires_at)));
      const button = node('button', entry.is_current ? 'Bu oturumu kapat' : 'Oturumu kapat', 'as-btn');
      button.type = 'button';
      button.addEventListener('click', () => {
        const password = sessionPassword();
        if (password === null) return;
        act(async () => {
          await requestJSON('/api/account-security/sessions/revoke', { current_password: password, session_id: entry.id });
          clearPasswords();
          if (entry.is_current) { location.assign('/login'); return; }
          await loadStatus(); message('Seçilen oturum kapatıldı.');
        });
      });
      row.append(description, button); list.appendChild(row);
    }
    $('#as-revoke-others').hidden = !state.sessions.some(entry => !entry.is_current);
  };

  if (mode === 'challenge') {
    $('#as-challenge-form').addEventListener('submit', event => {
      event.preventDefault(); const form = event.currentTarget;
      act(async () => {
        const result = await requestJSON('/login/verify', { code: form.elements.code.value.trim() });
        form.reset(); location.assign(result.redirect || '/workbench');
      });
    });
    $('#as-cancel-challenge').addEventListener('click', () => act(async () => {
      await requestJSON('/login/verify/cancel', {}); location.assign('/login');
    }));
    return;
  }
  $('#as-setup-form').addEventListener('submit', event => {
    event.preventDefault(); const form = event.currentTarget;
    act(async () => {
      const result = await requestJSON('/api/account-security/mfa/setup', { current_password: form.elements.current_password.value });
      $('#as-secret').textContent = result.secret;
      $('#as-setup-details').hidden = false;
      form.reset(); message('Kurulum anahtarı hazır. Doğrulayıcı uygulamaya ekleyip aşağıdaki kodu onaylayın.');
      $('#as-confirm-form').elements.code.focus();
    });
  });
  $('#as-copy-secret').addEventListener('click', () => act(() => copy($('#as-secret').textContent)));
  $('#as-confirm-form').addEventListener('submit', event => {
    event.preventDefault(); const form = event.currentTarget;
    act(async () => {
      const result = await requestJSON('/api/account-security/mfa/confirm', { current_password: form.elements.current_password.value, code: form.elements.code.value.trim() });
      $('#as-secret').textContent = ''; $('#as-setup-details').hidden = true;
      clearPasswords(); form.reset(); showRecovery(result.recovery_codes); await loadStatus();
      message('İki aşamalı doğrulama açıldı. Diğer açık oturumlar kapatıldı. Kurtarma kodlarını saklayın.');
    });
  });
  $('#as-factor-actions').addEventListener('submit', event => {
    event.preventDefault(); const form = event.currentTarget;
    const disable = event.submitter?.value === 'disable';
    act(async () => {
      const result = await requestJSON('/api/account-security/mfa/' + (disable ? 'disable' : 'recovery-codes'), { current_password: form.elements.current_password.value, code: form.elements.code.value.trim() });
      clearPasswords(); form.reset();
      if (disable) { $('#as-recovery-codes').textContent = ''; $('#as-recovery-section').hidden = true; }
      else { showRecovery(result.recovery_codes); }
      await loadStatus();
      message(disable ? 'İki aşamalı doğrulama kapatıldı. Diğer oturumlar ve eski kurtarma kodları geçersiz kılındı.' : 'Yeni kurtarma kodları hazır. Önceki kodlar geçersiz kılındı.');
    });
  });
  $('#as-copy-recovery').addEventListener('click', () => act(() => copy($('#as-recovery-codes').textContent)));
  $('#as-hide-recovery').addEventListener('click', () => { $('#as-recovery-codes').textContent = ''; $('#as-recovery-section').hidden = true; message('Kurtarma kodları ekrandan gizlendi.'); });
  $('#as-revoke-others').addEventListener('click', () => {
    const password = sessionPassword(); if (password === null) return;
    act(async () => { await requestJSON('/api/account-security/sessions/revoke', { current_password: password, all_others: true }); clearPasswords(); await loadStatus(); message('Diğer açık oturumlar kapatıldı.'); });
  });
  act(loadStatus);
})();
