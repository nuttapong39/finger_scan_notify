/* Appearance ของหน้าเว็บ: 'cyber' (ค่าเริ่มต้น) หรือ 'light' เท่านั้น — จำต่อ browser ไม่ตามค่าของระบบปฏิบัติการ (ADR 0003)
   ค่าเก่า 'dark' (ก่อนเปลี่ยนชื่อ) อ่านเป็น 'cyber' ; หน้าเข้าสู่ระบบไม่โหลดไฟล์นี้ (เป็น Cyber เสมอ)
   โหลดใน <head> แบบ sync เพื่อตั้งโหมดก่อนวาดหน้า (หน้าจะไม่กะพริบ) */
(function () {
  var KEY = 'appearance';

  function read() {
    try { return localStorage.getItem(KEY) === 'light' ? 'light' : 'cyber'; } catch (e) { return 'cyber'; }
  }
  function apply(mode) {
    document.documentElement.setAttribute('data-appearance', mode === 'light' ? 'light' : 'cyber');
  }
  function current() {
    return document.documentElement.getAttribute('data-appearance') === 'light' ? 'light' : 'cyber';
  }

  apply(read());

  document.addEventListener('DOMContentLoaded', function () {
    var btn = document.getElementById('appearance-toggle');
    if (!btn) return;
    function sync() {
      var cyber = current() === 'cyber';
      btn.setAttribute('aria-pressed', cyber ? 'true' : 'false');
      btn.title = cyber ? 'สลับเป็นโหมดสว่าง' : 'สลับเป็นโหมด Cyber';
      btn.setAttribute('aria-label', btn.title);
    }
    btn.addEventListener('click', function () {
      var next = current() === 'cyber' ? 'light' : 'cyber';
      try { localStorage.setItem(KEY, next); } catch (e) { /* โหมด private: ใช้ได้เฉพาะหน้านี้ */ }
      apply(next);
      sync();
      document.dispatchEvent(new CustomEvent('appearance-changed', { detail: next }));
    });
    sync();
  });
})();
