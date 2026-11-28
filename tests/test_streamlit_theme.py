"""Theme folder for the Streamlit demo: defaults, loading, escaping and refusals."""
import json

import pytest

from apps.streamlit import theme

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


def make(tmp_path, values=None, css=None, logo=None):
    if values is not None:
        (tmp_path / "theme.json").write_text(json.dumps(values), encoding="utf-8")
    if css is not None:
        (tmp_path / "style.css").write_text(css, encoding="utf-8")
    if logo is not None:
        (tmp_path / logo).write_bytes(PNG)
    return tmp_path


def test_no_folder_gives_neutral_defaults():
    loaded = theme.load_theme(None)
    assert loaded["title"] == "Social Listening MCP" and loaded["css"] == "" and loaded["logo_uri"] == ""
    assert theme.welcome_html(loaded) == ""


def test_folder_sets_texts_css_and_logo(tmp_path):
    folder = make(tmp_path, {"title": "Demo Analyst", "title_accent": "Analyst", "subtitle": "Agents",
                             "greeting": "Halo", "intro": "Tanya data.", "logo": "mark.png",
                             "sidebar": "collapsed"}, css="body{color:#111}", logo="mark.png")
    loaded = theme.load_theme(str(folder))
    assert loaded["css"] == "body{color:#111}" and loaded["sidebar"] == "collapsed"
    assert loaded["logo_uri"].startswith("data:image/png;base64,")
    header = theme.header_html(loaded)
    assert '<span class="app-accent">Analyst</span>' in header and "Agents" in header
    welcome = theme.welcome_html(loaded)
    assert "<h2>Halo</h2>" in welcome and loaded["logo_uri"] in welcome


def test_texts_are_escaped(tmp_path):
    loaded = theme.load_theme(str(make(tmp_path, {"title": "<script>x</script>", "greeting": "a & b"})))
    assert "<script>" not in theme.header_html(loaded)
    assert "a &amp; b" in theme.welcome_html(loaded)


@pytest.mark.parametrize("values, message", [
    ({"colour": "blue"}, "Unknown theme.json keys"),
    ({"sidebar": "hidden"}, "sidebar must be"),
    ({"logo": "../outside.png"}, "logo must be"),
    ({"logo": "notes.txt"}, "logo must be"),
])
def test_bad_settings_are_refused(tmp_path, values, message):
    (tmp_path / "notes.txt").write_text("x")
    with pytest.raises(theme.ThemeError, match=message):
        theme.load_theme(str(make(tmp_path, values)))


def test_missing_folder_and_bad_json_are_refused(tmp_path):
    with pytest.raises(theme.ThemeError, match="not found"):
        theme.load_theme(str(tmp_path / "nope"))
    (tmp_path / "theme.json").write_text("{bad")
    with pytest.raises(theme.ThemeError, match="not valid JSON"):
        theme.load_theme(str(tmp_path))
