/* Password strength meter for every "new password" box: <input data-strength>.
   Shows a bar (Weak / Fair / Good / Strong) and the two rules the server checks:
   at least 8 characters, and at least one letter and one number. */
(function () {
  'use strict';

  const LABELS = ['Too weak', 'Weak', 'Fair', 'Good', 'Strong'];

  function score(pw) {
    if (!pw) return 0;
    let s = 0;
    if (pw.length >= 8) s += 1;
    if (pw.length >= 12) s += 1;
    if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) s += 1;
    if (/[0-9]/.test(pw) && /[A-Za-z]/.test(pw)) s += 1;
    if (/[^A-Za-z0-9]/.test(pw)) s += 1;
    // The server's minimum (8+, letter, number) must be met before it can say more than "Weak"
    const meetsRule = pw.length >= 8 && /[A-Za-z]/.test(pw) && /[0-9]/.test(pw);
    if (!meetsRule) return Math.min(s, 1);
    if (/^(.)\1+$/.test(pw) || /^(?:12345678|password1|qwerty123)/i.test(pw)) return 1;
    return Math.max(2, Math.min(4, s));
  }

  function attach(input) {
    if (input.dataset.strengthReady) return;
    input.dataset.strengthReady = '1';
    const box = document.createElement('div');
    box.className = 'pw-meter';
    box.innerHTML = `
      <div class="pw-meter-bar" aria-hidden="true"><span></span><span></span><span></span><span></span></div>
      <span class="pw-meter-label" aria-live="polite"></span>
      <ul class="pw-rules">
        <li data-rule="len">At least 8 characters</li>
        <li data-rule="mix">At least one letter and one number</li>
      </ul>`;
    // After the field's own box (the input sits inside .input-icon on the log in pages)
    (input.closest('.input-icon') || input).after(box);
    const label = box.querySelector('.pw-meter-label');
    const rules = {
      len: box.querySelector('[data-rule="len"]'),
      mix: box.querySelector('[data-rule="mix"]'),
    };
    const update = () => {
      const pw = input.value;
      const s = score(pw);
      box.dataset.score = pw ? String(s) : '';
      label.textContent = pw ? LABELS[s] : '';
      rules.len.classList.toggle('is-ok', pw.length >= 8);
      rules.mix.classList.toggle('is-ok', /[A-Za-z]/.test(pw) && /[0-9]/.test(pw));
      const ok = pw.length >= 8 && /[A-Za-z]/.test(pw) && /[0-9]/.test(pw);
      input.setCustomValidity(pw && !ok ? 'Use at least 8 characters with at least one letter and one number.' : '');
    };
    input.addEventListener('input', update);
    update();
  }

  function init() { document.querySelectorAll('input[data-strength]').forEach(attach); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
