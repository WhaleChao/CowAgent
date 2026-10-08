# encoding:utf-8
"""The Web console renders ```mermaid fences as diagrams (#3221).

The render pipeline is browser JS, so pytest can only pin the static
contract: mermaid is lazy-loaded at runtime from a version-pinned CDN URL
verified by a subresource integrity hash (the #3221 review ruled out the
in-tree vendor copy), the fence rule turns mermaid fences into upgradeable
placeholders, the renderer runs with ``securityLevel: 'strict'`` (message
content is untrusted agent output), invalid syntax and a failed CDN load
both degrade back to a plain code block, a rendered diagram grows a
[Chart|Code] toggle plus zoom/fullscreen controls on the chart side, and a
theme switch re-renders finished diagrams.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "channel" / "web"
VENDOR = WEB / "static" / "vendor" / "mermaid" / "mermaid.min.js"
CDN_URL = "https://cdn.jsdelivr.net/npm/mermaid@11.17.2/dist/mermaid.min.js"
SRI_HASH = "sha384-EOXBFmc3gx5mb+vn0vPvvGqACToJD24hhacX5Yx+8NUUQrHIle/Qi5Bg9o3zKwW2"


def _read(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def _console_js():
    from conftest import console_js

    return console_js()


def test_mermaid_is_fetched_from_pinned_cdn_not_vendored():
    # Review decision on #3221: the ~3.5MB build is not carried in-tree;
    # it is fetched at runtime from a version-pinned URL instead.
    assert not VENDOR.exists()
    js = _console_js()
    assert f"const MERMAID_CDN_URL = '{CDN_URL}';" in js
    assert "script.src = MERMAID_CDN_URL;" in js
    readme = _read("channel/web/static/vendor/README.md")
    assert CDN_URL in readme, "the CDN source must be registered in the vendor manifest"
    assert "11.17.2" in readme


def test_cdn_load_is_sri_verified():
    # A pinned version alone does not protect against a tampered CDN: the
    # subresource integrity hash pins the exact build, and the README
    # manifest carries the same hash so a mismatch is visible in review.
    js = _console_js()
    match = re.search(r"const MERMAID_SRI_HASH = '(sha384-[^']+)';", js)
    assert match, "the CDN loader must define a subresource integrity hash"
    assert match.group(1) == SRI_HASH
    assert "script.integrity = MERMAID_SRI_HASH;" in js
    assert "script.crossOrigin = 'anonymous';" in js  # SRI requires CORS
    readme = _read("channel/web/static/vendor/README.md")
    assert SRI_HASH in readme


def test_mermaid_is_lazy_loaded_not_fetched_on_page_load():
    # ~3.5MB must not be fetched up front: chat.html carries no mermaid
    # script tag, the CDN script is injected on the first mermaid block.
    page = _read("channel/web/chat.html")
    assert not re.search(r"<script[^>]*mermaid", page)
    js = _console_js()
    assert "function ensureMermaidLoaded()" in js


def test_fence_rule_intercepts_only_mermaid_and_escapes_source():
    js = _console_js()
    assert "md.renderer.rules.fence" in js
    # Matched on the info string; every other fence keeps the default rule.
    assert "lang !== 'mermaid'" in js
    # The placeholder keeps the source in a real <pre><code> so the usual
    # code-block header still wraps it (copy button), and the source is
    # escaped: fence content is untrusted agent output.
    assert 'data-mermaid="pending"' in js
    assert "escapeHtml(token.content)" in js


def test_renderer_is_strict_idempotent_and_theme_aware():
    js = _console_js()
    assert "securityLevel: 'strict'" in js  # no HTML labels, no callbacks
    assert "startOnLoad: false" in js  # rendering is driven by applyHighlighting
    assert "suppressErrorRendering: true" in js  # no mermaid error graphic
    assert "classList.contains('dark') ? 'dark' : 'default'" in js
    # Only pending blocks are picked up, so repeated passes never re-render.
    assert '.mermaid-block[data-mermaid="pending"]' in js


def test_invalid_syntax_degrades_to_plain_code_block():
    js = _console_js()
    # A rejected render marks the block 'invalid' and keeps the <pre>:
    # the code block stays, header and copy button included.
    assert "block.dataset.mermaid = 'invalid'" in js
    assert "renderMermaidBlocks(root)" in js  # hooked into applyHighlighting


def test_failed_cdn_load_degrades_too():
    js = _console_js()
    # A failed lazy load (offline, blocked CDN, integrity mismatch) clears
    # its memo (retryable) and marks the pending blocks invalid instead of
    # leaving placeholders that never upgrade.
    assert "_mermaidLoadPromise = null" in js
    assert "Failed to load mermaid" in js


def test_rendered_diagram_gets_chart_code_toggle():
    js = _console_js()
    # After a successful render the code-block header grows a [Chart|Code]
    # toggle. Chart is the default view; the source <pre> always stays in
    # the DOM so code view (and its copy button) always has the source.
    assert "function _installMermaidToolbar(" in js
    assert 'data-mermaid-view="chart"' in js
    assert 'data-mermaid-view="code"' in js
    assert "dataset.mermaidMode = 'chart'" in js  # default view
    css = _read("channel/web/static/css/markdown.css")
    assert '.mermaid-block[data-mermaid-mode="code"]' in css
    # The copy button belongs to code view only, per the #3221 review.
    assert '.mermaid-block[data-mermaid-mode="chart"] .code-copy-btn' in css


def test_chart_view_has_zoom_and_fullscreen_controls():
    js = _console_js()
    # Zoom in / out / reset scale the rendered SVG; fullscreen prefers the
    # Fullscreen API and falls back to a CSS class on the block.
    assert 'data-mermaid-zoom="in"' in js
    assert 'data-mermaid-zoom="out"' in js
    assert 'data-mermaid-zoom="reset"' in js
    assert "function _setMermaidZoom(" in js
    assert "requestFullscreen" in js
    assert "exitFullscreen" in js
    assert "classList.toggle('mermaid-fullscreen')" in js
    css = _read("channel/web/static/css/markdown.css")
    assert ".mermaid-block.mermaid-fullscreen" in css


def test_theme_switch_re_renders_finished_diagrams():
    theme = _read("channel/web/static/js/core/theme.js")
    # applyTheme() is the single switch point (boot and toggleTheme both hit
    # it), and the re-render must reset the done markers for it.
    assert "rerenderMermaidDiagrams();" in theme
    assert theme.index("rerenderMermaidDiagrams();") < theme.index("function toggleTheme")
    assert 'data-mermaid="done"' in theme
    assert "renderMermaidBlocks(document)" in theme
