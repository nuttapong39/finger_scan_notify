"""หน้าตาเว็บ: ไอคอน Lucide / Simple Icons, ไม่มี emoji, ไฟล์ static, Appearance สว่าง/มืด

ทดสอบที่ขอบเขตเดียวคือ "สิ่งที่ browser ได้รับ" (HTML/CSS/SVG จาก Flask test client) ไม่ดูโครงสร้างภายใน
"""
import re

import pytest

import database

PAGES = ['/dashboard', '/logs', '/backfill', '/reminders', '/debug',
         '/config/database', '/config/api', '/config/tokens', '/account/password', '/watch']

EMOJI = re.compile('[\U0001F000-\U0001FAFF☀-➿⬀-⯿️⌀-⏿]')
USE_HREF = re.compile(r'<use\s+href="[^"]*icons\.svg[^"#]*#([a-z0-9-]+)"')
JS_ICON = re.compile(r"""\bicon\(\s*['"]([a-z0-9-]+)['"]""")
SYMBOL_ID = re.compile(r'<symbol\s+[^>]*id="([a-z0-9-]+)"')
KEEP_EMOJI = re.compile(r'<(\w+)[^>]*\bdata-keep-emoji\b[^>]*>.*?</\1>', re.S)


@pytest.fixture
def pages(admin_client):
    """HTML ของทุกหน้าหลังล็อกอิน (มี token ตัวอย่างสองแบบให้แถวในตารางถูกวาดด้วย)"""
    database.save_token(156, 'เกศรา ทดสอบ', 'MOPH_ALERT', '', 'ck', 'sk', 'งานสารสนเทศ', 1, 1)
    database.save_token(157, 'ณัฐพงษ์ ทดสอบ', 'LINE_NOTIFY', 'tok', '', '', 'งานพยาบาล', 1, 0)
    out = {}
    for path in PAGES:
        r = admin_client.get(path)
        assert r.status_code == 200, path
        out[path] = r.get_data(as_text=True)
    return out


@pytest.fixture
def modal_page(pages):
    """หน้าที่เห็นตอนยังใช้รหัสชั่วคราว (pop-up บังคับเปลี่ยนรหัส) — ออกรหัสชั่วคราวใหม่ให้ client ใหม่"""
    import app as app_module
    import auth
    temp = auth.create_admin(reset=True)
    c = app_module.app.test_client()
    c.environ_base['HTTP_ORIGIN'] = 'http://localhost'
    assert c.post('/login', data={'username': 'admin', 'password': temp}).status_code == 302
    html = c.get('/dashboard').get_data(as_text=True)
    assert 'force-password-modal' in html
    return html


@pytest.fixture
def login_page(tmp_db):
    """หน้า login ที่คนยังไม่ล็อกอินเห็น (client แยก เพราะ anon_client กับ admin_client เป็นตัวเดียวกัน)"""
    import app as app_module
    r = app_module.app.test_client().get('/login')
    assert r.status_code == 200
    return r.get_data(as_text=True)


def all_html(pages, modal_page, login_page):
    return {**pages, 'modal': modal_page, '/login': login_page}


def sprite_ids(client):
    r = client.get('/static/icons.svg')
    assert r.status_code == 200
    return set(SYMBOL_ID.findall(r.get_data(as_text=True)))


# ── ไอคอนครบ ───────────────────────────────────────────────
def test_every_icon_a_page_uses_exists_in_the_sprite(admin_client, pages, modal_page, login_page):
    known = sprite_ids(admin_client)
    assert len(known) >= 30
    for name, html in all_html(pages, modal_page, login_page).items():
        used = set(USE_HREF.findall(html)) | set(JS_ICON.findall(html))
        assert used, f'{name}: ไม่มีไอคอนเลย'
        assert used <= known, f'{name}: ไอคอนที่ไม่มีใน sprite: {sorted(used - known)}'


def test_brand_logos_sit_where_the_service_is_configured(pages):
    assert 'brand-mysql' in pages['/config/database']
    assert 'brand-line' in pages['/config/tokens']
    assert 'brand-sqlite' in pages['/config/database']


def test_sprite_is_lucide_and_simple_icons_only_no_external_fetch(admin_client):
    svg = admin_client.get('/static/icons.svg').get_data(as_text=True)
    assert 'http://' not in svg.replace('http://www.w3.org/2000/svg', '')
    assert 'https://' not in svg
    assert 'id="brand-line"' in svg and 'id="log-in"' in svg


# ── ไม่เหลือ emoji ─────────────────────────────────────────
def test_no_emoji_left_as_icons_on_any_page(pages, modal_page, login_page):
    for name, html in all_html(pages, modal_page, login_page).items():
        text = KEEP_EMOJI.sub('', html)
        found = sorted(set(EMOJI.findall(text)))
        assert not found, f'{name}: ยังมี emoji {found}'


def test_emoji_inside_a_sample_of_the_real_line_message_is_the_only_exemption(pages):
    # ตัวอย่างข้อความ Line Notify เลียนแบบข้อความจริงที่ส่งเข้า LINE (มี emoji จริง) จึงเว้นไว้ตรงนี้ที่เดียว
    assert 'data-keep-emoji' in pages['/config/api']
    for path, html in pages.items():
        if path != '/config/api':
            assert 'data-keep-emoji' not in html, path


# ── ไฟล์ static + หน้า login ───────────────────────────────
def test_static_assets_open_without_logging_in(anon_client):
    for path, kind in [('/static/icons.svg', 'image/svg+xml'), ('/static/app.css', 'text/css'),
                       ('/static/logo_ck.png', 'image/png'), ('/static/appearance.js', 'javascript')]:
        r = anon_client.get(path)
        assert r.status_code == 200, path
        assert kind in r.content_type, (path, r.content_type)
        assert r.get_data(), path


def test_unknown_static_file_is_404(anon_client):
    assert anon_client.get('/static/nope.svg').status_code == 404
    assert anon_client.get('/static/../app.py').status_code == 404


def test_login_page_uses_own_logo_favicon_and_the_shared_stylesheet(login_page):
    assert 'src="/static/logo_ck.png' in login_page
    assert re.search(r'<link[^>]+rel="icon"[^>]+logo_ck\.png', login_page)
    assert '/static/app.css' in login_page
    assert 'ckhospital.net' not in login_page          # โลโก้ต้องไม่พึ่งเว็บภายนอก


def test_every_page_has_the_hospital_logo_and_favicon(pages, modal_page):
    for name, html in {**pages, 'modal': modal_page}.items():
        assert re.search(r'<link[^>]+rel="icon"', html), name
    assert 'src="/static/logo_ck.png' in pages['/dashboard']


# ── Appearance: สว่าง/มืด ─────────────────────────────────
def test_every_page_has_the_appearance_toggle_and_sets_it_before_first_paint(pages, modal_page, login_page):
    for name, html in all_html(pages, modal_page, login_page).items():
        assert 'id="appearance-toggle"' in html, name
        head = html.split('<body', 1)[0]
        assert '/static/appearance.js' in head, f'{name}: ต้องตั้งโหมดสีใน <head> ไม่งั้นหน้ากะพริบ'
        assert '<html lang="th">' in html, name            # server ไม่บังคับโหมดมืด → เริ่มเป็นสว่างเสมอ


def test_appearance_script_defaults_to_light_and_only_accepts_dark(admin_client):
    js = admin_client.get('/static/appearance.js').get_data(as_text=True)
    assert "'light'" in js and "'dark'" in js
    assert 'prefers-color-scheme' not in js            # ไม่ตามค่าของระบบปฏิบัติการ
    assert 'data-appearance' in js or 'dataset.appearance' in js
    assert 'localStorage' in js


def test_stylesheet_has_light_as_root_and_dark_as_the_only_alternative(admin_client):
    css = admin_client.get('/static/app.css').get_data(as_text=True)
    assert 'prefers-color-scheme' not in css
    assert '[data-appearance="dark"]' in css
    assert re.search(r':root\s*\{', css)
    assert '[data-appearance="light"]' not in css       # มีแค่ Light (ค่าเริ่มต้น) กับ Dark
    assert re.search(r'--shadow:\s*none', css)           # Minimal: การ์ดไม่มีเงา


def test_dark_overrides_every_color_token_that_light_defines(admin_client):
    css = admin_client.get('/static/app.css').get_data(as_text=True)
    light = re.search(r':root\s*\{(.*?)\}', css, re.S).group(1)
    dark = re.search(r'\[data-appearance="dark"\]\s*\{(.*?)\}', css, re.S).group(1)
    tokens = lambda block: set(re.findall(r'(--(?:bg|border|text|accent|tint)[a-z0-9-]*)\s*:', block))
    assert tokens(light), 'ไม่พบ token สีใน :root'
    assert tokens(light) <= tokens(dark), sorted(tokens(light) - tokens(dark))


# ── ตัวอักษรย่อบุคลากร (ไม่ใช้รูปถ่าย) ────────────────────
def test_initial_skips_leading_thai_vowels():
    import icons
    assert icons.initial('เกศรา') == 'ก'
    assert icons.initial('ณัฐพงษ์') == 'ณ'
    assert icons.initial('  แพทย์หญิง') == 'พ'
    assert icons.initial('') == '?'
    assert icons.initial(None) == '?'
    assert icons.initial('alice') == 'A'


def test_token_list_shows_initial_avatars_not_photos(pages):
    html = pages['/config/tokens']
    assert re.search(r'class="avatar"[^>]*>ก<', html)
    assert re.search(r'class="avatar"[^>]*>ณ<', html)
    assert '<img' not in html.split('id="token-table"', 1)[-1].split('</table>', 1)[0]


def test_nginx_does_not_block_the_static_assets():
    import pathlib
    conf = (pathlib.Path(__file__).parents[2] / 'deploy' / 'nginx-scan-notify.conf').read_text(encoding='utf-8')
    deny = re.search(r'location ~\* \\\.\(([^)]*)\)\$ \{ deny all', conf).group(1).split('|')
    assert not {'svg', 'png', 'css', 'js'} & set(deny)


def test_css_js_and_logo_urls_carry_a_version_so_browsers_do_not_keep_stale_files(login_page):
    for name in ('app.css', 'appearance.js', 'logo_ck.png', 'icons.svg'):
        pattern = rf'/static/{re.escape(name)}\?v=\d+' if name != 'icons.svg' else None
        if pattern:
            assert re.search(pattern, login_page), name
    # sprite ถูกเรียกผ่าน <use> ซึ่งอยู่ในทุกหน้าที่มีไอคอน
    assert re.search(r'icons\.svg\?v=\d+#', login_page)


def test_post_deploy_smoke_checks_the_static_files_through_nginx():
    import pathlib
    smoke = (pathlib.Path(__file__).parents[2] / 'deploy' / 'smoke_after_deploy.sh').read_text(encoding='utf-8')
    for name in ('app.css', 'appearance.js', 'icons.svg', 'logo_ck.png'):
        assert f'static/{name}' in smoke
