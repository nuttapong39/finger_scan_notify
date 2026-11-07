import pytest

from flex_theme import DEFAULT_THEME, validate_theme, merge_theme


def test_defaults_are_valid():
    clean, errors = validate_theme(dict(DEFAULT_THEME))
    assert errors == {}
    assert clean == DEFAULT_THEME


def test_colors_are_normalised_to_uppercase_hex():
    clean, errors = validate_theme({**DEFAULT_THEME, 'checkin_start': '#aabbcc'})
    assert errors == {}
    assert clean['checkin_start'] == '#AABBCC'


@pytest.mark.parametrize('bad', ['red', '#12345', '#GGGGGG', '123456', '', None, 123, '#12345678'])
def test_bad_color_is_rejected(bad):
    _, errors = validate_theme({**DEFAULT_THEME, 'checkout_end': bad})
    assert 'checkout_end' in errors


@pytest.mark.parametrize('angle', [0, 90, 135, 360, '45'])
def test_angle_accepts_0_to_360(angle):
    clean, errors = validate_theme({**DEFAULT_THEME, 'angle': angle})
    assert errors == {}
    assert clean['angle'] == int(angle)


@pytest.mark.parametrize('angle', [-1, 361, 'abc', None, 1.5, True])
def test_angle_out_of_range_rejected(angle):
    _, errors = validate_theme({**DEFAULT_THEME, 'angle': angle})
    assert 'angle' in errors


def test_labels_are_stripped_and_length_limited():
    clean, errors = validate_theme({**DEFAULT_THEME, 'checkin_label': '  เข้าปฏิบัติงาน  '})
    assert errors == {} and clean['checkin_label'] == 'เข้าปฏิบัติงาน'
    _, errors = validate_theme({**DEFAULT_THEME, 'checkin_label': '   '})
    assert 'checkin_label' in errors
    _, errors = validate_theme({**DEFAULT_THEME, 'checkout_label': 'ก' * 21})
    assert 'checkout_label' in errors


def test_footer_length_limited():
    _, errors = validate_theme({**DEFAULT_THEME, 'footer_text': 'x' * 61})
    assert 'footer_text' in errors
    _, errors = validate_theme({**DEFAULT_THEME, 'footer_text': ''})
    assert 'footer_text' in errors


def test_missing_key_is_an_error_but_unknown_key_is_ignored():
    partial = {k: v for k, v in DEFAULT_THEME.items() if k != 'angle'}
    _, errors = validate_theme(partial)
    assert 'angle' in errors
    clean, errors = validate_theme({**DEFAULT_THEME, 'evil': '<script>'})
    assert errors == {} and 'evil' not in clean


def test_non_dict_is_rejected():
    _, errors = validate_theme(None)
    assert errors
    _, errors = validate_theme([1, 2])
    assert errors


def test_merge_fills_defaults_and_drops_invalid_fields():
    merged = merge_theme({'checkin_start': '#112233', 'angle': 999, 'footer_text': 'ข้อความของเรา'})
    assert merged['checkin_start'] == '#112233'
    assert merged['footer_text'] == 'ข้อความของเรา'
    assert merged['angle'] == DEFAULT_THEME['angle']                    # invalid → default
    assert merged['checkout_start'] == DEFAULT_THEME['checkout_start']  # missing → default


def test_merge_of_garbage_is_defaults():
    assert merge_theme(None) == DEFAULT_THEME
    assert merge_theme('nope') == DEFAULT_THEME
