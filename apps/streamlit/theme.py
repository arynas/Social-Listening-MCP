"""Optional look for the Streamlit demo, loaded from a folder named by SLMCP_THEME_DIR.

The folder may hold:
  theme.json  texts and options (all optional):
              {"page_title", "title", "title_accent", "subtitle", "greeting", "intro",
               "logo": "logo.png", "sidebar": "auto" | "expanded" | "collapsed"}
  style.css   extra CSS for the page
  the logo file named in theme.json (PNG, SVG or JPEG)

Without a folder the page uses the neutral defaults below. Brand assets stay outside the repository.
"""

import base64
import json
from html import escape
from pathlib import Path

DEFAULTS = {
    "page_title": "Social Listening MCP demo",
    "title": "Social Listening MCP",
    "title_accent": "",
    "subtitle": "",
    "greeting": "",
    "intro": "",
    "logo": "",
    "sidebar": "auto",
}
LOGO_TYPES = {".png": "image/png", ".svg": "image/svg+xml", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class ThemeError(ValueError):
    """The theme folder exists but cannot be used."""


def load_theme(folder):
    """Theme settings, CSS text and logo data URI. folder may be None or empty for the defaults."""
    theme = dict(DEFAULTS, css="", logo_uri="")
    if not folder:
        return theme
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise ThemeError(f"Theme folder not found: {root}")
    config = root / "theme.json"
    if config.is_file():
        try:
            values = json.loads(config.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ThemeError(f"theme.json is not valid JSON: {error.msg}") from error
        unknown = set(values) - set(DEFAULTS)
        if unknown:
            raise ThemeError(f"Unknown theme.json keys: {', '.join(sorted(unknown))}")
        theme.update({key: str(value) for key, value in values.items()})
    if theme["sidebar"] not in ("auto", "expanded", "collapsed"):
        raise ThemeError("sidebar must be auto, expanded or collapsed")
    css = root / "style.css"
    if css.is_file():
        theme["css"] = css.read_text(encoding="utf-8")
    if theme["logo"]:
        logo = (root / theme["logo"]).resolve()
        if root.resolve() not in logo.parents or logo.suffix.lower() not in LOGO_TYPES or not logo.is_file():
            raise ThemeError("logo must be a PNG, SVG or JPEG file inside the theme folder")
        data = base64.b64encode(logo.read_bytes()).decode()
        theme["logo_uri"] = f"data:{LOGO_TYPES[logo.suffix.lower()]};base64,{data}"
    return theme


def header_html(theme):
    """Title (with an optional accent word) and subtitle as HTML, with all texts escaped."""
    title, accent = escape(theme["title"]), escape(theme["title_accent"])
    if accent and accent in title:
        title = title.replace(accent, f'<span class="app-accent">{accent}</span>', 1)
    subtitle = f'<p class="app-subtitle">{escape(theme["subtitle"])}</p>' if theme["subtitle"] else ""
    return f'<div class="app-header"><h1 class="app-title">{title}</h1>{subtitle}</div>'


def welcome_html(theme):
    """Empty-chat greeting with the optional logo; empty when the theme sets no greeting."""
    if not theme["greeting"]:
        return ""
    logo = f'<div class="app-logo"><img src="{theme["logo_uri"]}" alt=""></div>' if theme["logo_uri"] else ""
    intro = f'<p class="app-intro">{escape(theme["intro"])}</p>' if theme["intro"] else ""
    return f'<div class="app-welcome">{logo}<h2>{escape(theme["greeting"])}</h2>{intro}</div>'
