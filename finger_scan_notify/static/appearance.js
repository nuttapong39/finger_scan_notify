/* Appearance ของหน้าเว็บ: 'light' (ค่าเริ่มต้น) หรือ 'dark' เท่านั้น — จำต่อ browser ไม่ตามค่าของระบบปฏิบัติการ
   โหลดใน <head> แบบ sync เพื่อตั้งโหมดก่อนวาดหน้า (หน้าจะไม่กะพริบ) */
(function () {
  var KEY = 'appearance';

  function read() {
    try { return localStorage.getItem(KEY) === 'dark' ? 'dark' : 'light'; } catch (e) { return 'light'; }
  }
  function apply(mode) {
    document.documentElement.setAttribute('data-appearance', mode === 'dark' ? 'dark' : 'light');
  }
  function current() {
    return document.documentElement.getAttribute('data-appearance') === 'dark' ? 'dark' : 'light';
  }

  apply(read());

  document.addEventListener('DOMContentLoaded', function () {
    var btn = document.getElementById('appearance-toggle');
    if (!btn) return;
    function sync() {
      var dark = current() === 'dark';
      btn.setAttribute('aria-pressed', dark ? 'true' : 'false');
      btn.title = dark ? 'สลับเป็นโหมดสว่าง' : 'สลับเป็นโหมดมืด';
      btn.setAttribute('aria-label', btn.title);
    }
    btn.addEventListener('click', function () {
      var next = current() === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem(KEY, next); } catch (e) { /* โหมด private: ใช้ได้เฉพาะหน้านี้ */ }
      apply(next);
      sync();
      document.dispatchEvent(new CustomEvent('appearance-changed', { detail: next }));
    });
    sync();
  });
})();
