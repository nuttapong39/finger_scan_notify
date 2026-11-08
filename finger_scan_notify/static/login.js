/* หน้าเข้าสู่ระบบ (Cyber / Terminal) — เอฟเฟกต์ล้วน ๆ ฟอร์มส่งได้ปกติแม้ไม่มีสคริปต์นี้
   ข้อความใน "terminal" และ "scanner" เป็นของจำลอง ไม่ใช่ข้อมูลจริงของระบบ */
(function () {
  'use strict';
  var root = document.documentElement;
  var $ = function (id) { return document.getElementById(id); };
  var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ── FX เปิด/ปิด (จำใน browser) ── */
  var fxBtn = $('fxToggle');
  function fxOn() { return !root.classList.contains('fx-off'); }
  function syncFx() {
    fxBtn.textContent = fxOn() ? 'FX:ON' : 'FX:OFF';
    fxBtn.setAttribute('aria-pressed', fxOn() ? 'true' : 'false');
    if (fxOn()) startMatrix(); else stopMatrix();
  }
  fxBtn.addEventListener('click', function () {
    root.classList.toggle('fx-off');
    try { localStorage.setItem('ckh-fx', fxOn() ? 'on' : 'off'); } catch (e) { /* โหมด private */ }
    syncFx();
  });

  /* ── ฝนตัวอักษร (จำกัด ~20 เฟรม/วินาที หยุดเมื่อแท็บถูกซ่อน) ── */
  var cv = $('matrix'), ctx = cv.getContext && cv.getContext('2d'), timer = null, drops = [], size = 16;
  var glyphs = '01ABCDEF<>/\\{}[]=+*#$%'.split('');
  function resize() {
    cv.width = innerWidth; cv.height = innerHeight;
    var cols = Math.ceil(cv.width / size);
    while (drops.length < cols) drops.push(Math.random() * -40);
    drops.length = cols;
  }
  function frame() {
    ctx.fillStyle = 'rgba(3, 11, 8, .14)';
    ctx.fillRect(0, 0, cv.width, cv.height);
    ctx.font = size + 'px "IBM Plex Mono", monospace';
    for (var i = 0; i < drops.length; i++) {
      ctx.fillStyle = Math.random() > .96 ? '#d7fff0' : '#00ff9c';
      ctx.fillText(glyphs[(Math.random() * glyphs.length) | 0], i * size, drops[i] * size);
      if (drops[i] * size > cv.height && Math.random() > .975) drops[i] = 0;
      drops[i] += 1;
    }
  }
  function startMatrix() { if (ctx && !timer && !document.hidden) { resize(); timer = setInterval(frame, 50); } }
  function stopMatrix() { if (timer) { clearInterval(timer); timer = null; } if (ctx) ctx.clearRect(0, 0, cv.width, cv.height); }
  addEventListener('resize', function () { if (timer) resize(); });
  document.addEventListener('visibilitychange', function () { if (document.hidden) { if (timer) { clearInterval(timer); timer = null; } } else if (fxOn()) startMatrix(); });

  /* ── นาฬิกา / ปี (เวลาของเครื่องที่เปิดหน้า) ── */
  var days = ['อา.', 'จ.', 'อ.', 'พ.', 'พฤ.', 'ศ.', 'ส.'], p2 = function (n) { return (n < 10 ? '0' : '') + n; };
  function tick() {
    var d = new Date();
    $('hudClock').textContent = p2(d.getHours()) + ':' + p2(d.getMinutes()) + ':' + p2(d.getSeconds());
    $('hudDate').textContent = days[d.getDay()] + ' ' + p2(d.getDate()) + '/' + p2(d.getMonth() + 1) + '/' + (d.getFullYear() + 543);
  }
  tick(); setInterval(tick, 1000);
  $('yr').textContent = new Date().getFullYear() + 543;

  /* ── ข้อความพิมพ์ทีละตัว ── */
  var typer = $('typer'), lines = ['ระบบแจ้งเตือนสแกนนิ้วเข้า-ออกงาน', 'กรุณายืนยันตัวตนเพื่อเข้าสู่ระบบ'], li = 0;
  if (!reduce) {
    (function type(text, i, erasing) {
      typer.textContent = text.slice(0, i);
      if (!erasing && i < text.length) return setTimeout(function () { type(text, i + 1, false); }, 55);
      if (!erasing) return setTimeout(function () { type(text, i, true); }, 2600);
      if (i > 0) return setTimeout(function () { type(text, i - 1, true); }, 25);
      li = (li + 1) % lines.length; setTimeout(function () { type(lines[li], 0, false); }, 300);
    })(lines[0], lines[0].length, true);
  }

  /* ── terminal / hex จำลอง ── */
  var term = $('termLog'), hex = $('hexStream');
  var logs = [['ok', 'load module render'], ['inf', 'mount /static'], ['ok', 'check session store'], ['inf', 'wait credentials'],
              ['ok', 'render demo stream'], ['inf', 'idle']];
  function line(cls, text) {
    var r = document.createElement('div'); r.className = 'row';
    var t = document.createElement('span'); t.className = cls; t.textContent = '[' + (cls === 'ok' ? ' ok ' : 'info') + '] ';
    r.appendChild(t); r.appendChild(document.createTextNode(text)); return r;
  }
  function pushLog() {
    var l = logs[(Math.random() * logs.length) | 0];
    term.appendChild(line(l[0], l[1] + ' #' + ((Math.random() * 0xffff) | 0).toString(16)));
    while (term.children.length > 14) term.removeChild(term.firstChild);
  }
  function pushHex() {
    var s = ''; for (var i = 0; i < 8; i++) s += ((Math.random() * 256) | 0).toString(16).padStart(2, '0') + ' ';
    hex.textContent = (hex.textContent + s + '\n').split('\n').slice(-7).join('\n');
  }
  if (!reduce) { for (var k = 0; k < 8; k++) pushLog(); setInterval(function () { if (fxOn()) { pushLog(); pushHex(); } }, 900); }

  /* ── ฟอร์ม: แสดงรหัสผ่าน / Caps Lock / สถานะกำลังตรวจสอบ ── */
  var pw = $('password'), eye = $('togglePw'), warn = $('kbdWarn');
  eye.addEventListener('click', function () {
    var show = pw.type === 'password';
    pw.type = show ? 'text' : 'password';
    eye.setAttribute('aria-pressed', show ? 'true' : 'false');
    eye.setAttribute('aria-label', show ? 'ซ่อนรหัสผ่าน' : 'แสดงรหัสผ่าน');
    pw.focus();
  });
  ['keydown', 'keyup'].forEach(function (ev) {
    pw.addEventListener(ev, function (e) {
      warn.textContent = e.getModifierState && e.getModifierState('CapsLock') ? 'Caps Lock เปิดอยู่' : '';
    });
  });
  $('loginForm').addEventListener('submit', function () {
    var b = $('loginBtn'); b.classList.add('loading'); b.disabled = true;
  });
  addEventListener('pageshow', function (e) { if (e.persisted) { var b = $('loginBtn'); b.classList.remove('loading'); b.disabled = false; } });

  syncFx();
})();
