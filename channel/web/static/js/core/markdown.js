/* markdown-it setup plus image, video and code-block rendering.
   Split out of console.js. These are classic scripts sharing one global
   scope; see channel/web/README.md before changing the load order. */

// =====================================================================
// Markdown Renderer
// =====================================================================
const FALLBACK_HLJS = {
    getLanguage() { return false; },
    highlight(str) { return { value: escapeHtml(str) }; },
    highlightAuto(str) { return { value: escapeHtml(str) }; },
    highlightElement() {},
};

function getHljs() {
    return window.hljs || FALLBACK_HLJS;
}

// CJK ideographs, kana, Hangul and full/halfwidth forms (BMP only).
const CJK_CHAR_RE = /[\u1100-\u11FF\u2E80-\u303F\u3040-\u33FF\u3400-\u4DBF\u4E00-\u9FFF\uA960-\uA97F\uAC00-\uD7FF\uF900-\uFAFF\uFE10-\uFE19\uFE30-\uFE6F\uFF00-\uFF60\uFFE0-\uFFE6]/;

// CommonMark's flanking rules treat every Unicode punctuation alike, so
// `是**"引号"**——` never opens emphasis: the quote after `**` is punctuation
// while 是 before it is neither punctuation nor space, and the run degrades to
// literal asterisks. Apply the CJK-friendly amendment
// (github.com/tats-u/markdown-cjk-friendly): a `*` run with a CJK neighbour and
// no adjacent whitespace both opens and closes. `_` keeps the stock rules,
// whose intraword behavior depends on the original classification.
function patchCjkEmphasis(md) {
    const State = md.inline && md.inline.State;
    if (!State || !State.prototype.scanDelims || State.prototype._cjkEmphasisPatched) return;
    const utils = md.utils;
    const scanDelims = State.prototype.scanDelims;
    State.prototype.scanDelims = function(start, canSplitWord) {
        const res = scanDelims.call(this, start, canSplitWord);
        if (!canSplitWord) return res;
        const lastCode = start > 0 ? this.src.charCodeAt(start - 1) : 0x20;
        const nextPos = start + res.length;
        const nextCode = nextPos < this.posMax ? this.src.charCodeAt(nextPos) : 0x20;
        if (utils.isWhiteSpace(lastCode) || utils.isWhiteSpace(nextCode)) return res;
        if (!CJK_CHAR_RE.test(String.fromCharCode(lastCode)) &&
            !CJK_CHAR_RE.test(String.fromCharCode(nextCode))) return res;
        res.can_open = true;
        res.can_close = true;
        return res;
    };
    State.prototype._cjkEmphasisPatched = true;
}

function createMd() {
    const hljsLib = getHljs();
    const mdFactory = window.markdownit;
    if (typeof mdFactory !== 'function') {
        return {
            render(text) {
                return `<p>${escapeHtml(text || '')}</p>`;
            }
        };
    }
    const md = mdFactory({
        html: false, breaks: true, linkify: true, typographer: true,
        highlight: function(str, lang) {
            if (lang && hljsLib.getLanguage(lang)) {
                try { return hljsLib.highlight(str, { language: lang }).value; } catch (_) {}
            }
            return hljsLib.highlightAuto(str).value;
        }
    });
    patchCjkEmphasis(md);
    // Fix greedy linkify: markdown-it's linkify swallows markdown emphasis (*)
    // and CJK full-width punctuation glued to a URL (common in LLM output like
    // "**https://x**，中文"), turning the whole tail into one broken link. Cut
    // the URL at the first such char and spill the remainder back as text.
    var GREEDY_LINK_CUT = /[*\u3000-\u303F\uFF00-\uFFEF]/;
    md.core.ruler.after('linkify', 'fix_greedy_linkify', function(state) {
        for (var b = 0; b < state.tokens.length; b++) {
            var blk = state.tokens[b];
            if (blk.type !== 'inline' || !blk.children) continue;
            var ch = blk.children;
            for (var i = 0; i < ch.length; i++) {
                var open = ch[i];
                if (open.type !== 'link_open' || open.markup !== 'linkify') continue;
                var textTok = ch[i + 1], close = ch[i + 2];
                if (!textTok || textTok.type !== 'text' || !close || close.type !== 'link_close') continue;
                var idx = textTok.content.search(GREEDY_LINK_CUT);
                if (idx < 0) continue;
                var keep = textTok.content.slice(0, idx);
                var spill = textTok.content.slice(idx);
                textTok.content = keep;
                open.attrSet('href', keep);
                var spillTok = new state.Token('text', '', 0);
                spillTok.content = spill;
                ch.splice(i + 3, 0, spillTok);
            }
        }
    });
    const defaultLinkOpen = md.renderer.rules.link_open || function(tokens, idx, options, env, self) {
        return self.renderToken(tokens, idx, options);
    };
    md.renderer.rules.link_open = function(tokens, idx, options, env, self) {
        const token = tokens[idx];
        // A workspace-relative href would resolve against the console URL and
        // 404 in a new tab. Tag it instead so the click handler in
        // workspace.js opens it in the preview panel.
        const wsPath = typeof wsWorkspaceHref === 'function'
            ? wsWorkspaceHref(token.attrGet('href') || '') : null;
        if (wsPath) {
            token.attrPush(['data-ws-path', wsPath]);
            token.attrJoin('class', 'ws-link');
        } else {
            token.attrPush(['target', '_blank']);
            token.attrPush(['rel', 'noopener noreferrer']);
        }
        return defaultLinkOpen(tokens, idx, options, env, self);
    };
    // A table can't shrink below its columns' minimum content width, so a wide
    // comparison table would run past the bubble. Wrap it in a scroller: it
    // still fills the bubble when it fits and scrolls sideways when it doesn't.
    const defaultTableOpen = md.renderer.rules.table_open || function(tokens, idx, options, env, self) {
        return self.renderToken(tokens, idx, options);
    };
    const defaultTableClose = md.renderer.rules.table_close || function(tokens, idx, options, env, self) {
        return self.renderToken(tokens, idx, options);
    };
    md.renderer.rules.table_open = function(tokens, idx, options, env, self) {
        return '<div class="table-wrap">' + defaultTableOpen(tokens, idx, options, env, self);
    };
    md.renderer.rules.table_close = function(tokens, idx, options, env, self) {
        return defaultTableClose(tokens, idx, options, env, self) + '</div>';
    };
    // A ```mermaid fence is not highlighted as code: it becomes a placeholder
    // that renderMermaidBlocks() upgrades to a real diagram once the DOM is in
    // place (see applyHighlighting). Streaming-safe by construction: while the
    // fence is still open mermaid rejects the incomplete source and the block
    // simply stays a plain code block, so nothing flickers until the syntax
    // completes.
    const defaultFence = md.renderer.rules.fence;
    md.renderer.rules.fence = function(tokens, idx, options, env, self) {
        const token = tokens[idx];
        const lang = (token.info || '').trim().split(/\s+/)[0].toLowerCase();
        if (lang !== 'mermaid') {
            return defaultFence
                ? defaultFence(tokens, idx, options, env, self)
                : self.renderToken(tokens, idx, options);
        }
        // The source stays in a real <pre><code> so the usual code-block
        // header (with its copy button) still wraps it, and so the diagram
        // can be re-rendered from source on a theme switch.
        return '<div class="mermaid-block" data-mermaid="pending">' +
            '<pre><code class="language-mermaid">' + escapeHtml(token.content) + '</code></pre>' +
            '</div>';
    };
    return md;
}

const md = createMd();

const VIDEO_EXT_RE = /\.(?:mp4|webm|mov|avi|mkv)$/i;  // tested against URL without query string
const IMAGE_EXT_RE = /\.(?:jpg|jpeg|png|gif|webp|bmp|svg)$/i;  // tested against URL without query string

// Windows absolute path (D:\x.png / D:/x.png).
const WIN_ABS_PATH_RE = /^[A-Za-z]:[\\/]/;

function _toWebUrl(url) {
    if ((/^\/[A-Za-z]/.test(url) || WIN_ABS_PATH_RE.test(url)) && !url.startsWith('/api/')) {
        return '/api/file?path=' + encodeURIComponent(url);
    }
    if (/^file:\/\/\//i.test(url)) {
        // file:///home/x → /home/x, but file:///D:/x stays drive-relative.
        const p = url.replace(/^file:\/\/\//i, '');
        return '/api/file?path=' + encodeURIComponent(WIN_ABS_PATH_RE.test(p) ? p : '/' + p);
    }
    return url;
}

function _buildVideoHtml(url) {
    const webUrl = _toWebUrl(url);
    const fileName = url.split('/').pop().split('?')[0];
    return `<div style="margin:10px 0;">` +
        `<video controls preload="metadata" ` +
        `style="max-width:100%;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.15);display:block;">` +
        `<source src="${webUrl}"></video>` +
        `<a href="${webUrl}" target="_blank" ` +
        `style="display:inline-flex;align-items:center;gap:4px;margin-top:4px;font-size:12px;color:#8b8fa8;text-decoration:none;">` +
        `<i class="fas fa-download"></i> ${escapeHtml(fileName)}</a></div>`;
}

function _openImageLightbox(src) {
    let overlay = document.getElementById('cow-lightbox');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'cow-lightbox';
        overlay.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.85);display:flex;align-items:center;justify-content:center;cursor:zoom-out;opacity:0;transition:opacity .2s';
        overlay.onclick = () => { overlay.style.opacity = '0'; setTimeout(() => overlay.style.display = 'none', 200); };
        const img = document.createElement('img');
        img.id = 'cow-lightbox-img';
        img.style.cssText = 'max-width:92vw;max-height:92vh;border-radius:8px;box-shadow:0 4px 24px rgba(0,0,0,0.5);object-fit:contain;';
        img.onclick = (e) => e.stopPropagation();
        overlay.appendChild(img);
        document.body.appendChild(overlay);
    }
    overlay.querySelector('#cow-lightbox-img').src = src;
    overlay.style.display = 'flex';
    requestAnimationFrame(() => overlay.style.opacity = '1');
}

function _buildImageHtml(url) {
    const webUrl = _toWebUrl(url);
    const safeUrl = webUrl.replace(/"/g, '&quot;');
    return `<div style="margin:10px 0;">` +
        `<img src="${safeUrl}" alt="image" loading="lazy" ` +
        `onclick="_openImageLightbox(this.src)" ` +
        `style="max-width:520px;width:100%;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,0.15);display:block;cursor:zoom-in;">` +
        `</div>`;
}

function injectVideoPlayers(html) {
    // Step 1: replace markdown-it anchor tags whose href points to a video file.
    const step1 = html.replace(
        /<a\s+href="(https?:\/\/[^"]+)"[^>]*>[^<]*<\/a>/gi,
        (match, url) => VIDEO_EXT_RE.test(url.split('?')[0]) ? _buildVideoHtml(url) : match
    );
    // Step 2: replace any remaining bare video URLs in text nodes (not inside HTML tags).
    // Split on HTML tags to avoid touching src/href attributes already in markup.
    return step1.split(/(<[^>]+>)/).map((chunk, idx) => {
        // Even indices are text nodes; odd indices are HTML tags — leave them untouched.
        if (idx % 2 !== 0) return chunk;
        return chunk.replace(/https?:\/\/\S+/gi, (url) => {
            const bare = url.replace(/[),.\s]+$/, '');  // strip trailing punctuation
            return VIDEO_EXT_RE.test(bare.split('?')[0]) ? _buildVideoHtml(bare) : url;
        });
    }).join('');
}

// Convert image URLs into inline <img> previews. Mirrors injectVideoPlayers but for images.
// Handles three cases produced by markdown-it:
//   1. <a href="...image.jpg">...</a>  (bare URL or autolink that linkify turned into an anchor)
//   2. <img src="...">                  (markdown image syntax) — leave as-is, but normalize style
//   3. raw URL still present in a text node                    — only as a safety net
function injectImagePreviews(html) {
    // Step 1: anchor whose href points to an image file -> replace with <img> preview.
    const step1 = html.replace(
        /<a\s+href="(https?:\/\/[^"]+)"[^>]*>[^<]*<\/a>/gi,
        (match, url) => IMAGE_EXT_RE.test(url.split('?')[0]) ? _buildImageHtml(url) : match
    );
    // Step 2: bare image URLs left in text nodes (rare — markdown-it's linkify usually catches them).
    return step1.split(/(<[^>]+>)/).map((chunk, idx) => {
        if (idx % 2 !== 0) return chunk;
        return chunk.replace(/https?:\/\/\S+/gi, (url) => {
            const bare = url.replace(/[),.\s]+$/, '');
            return IMAGE_EXT_RE.test(bare.split('?')[0]) ? _buildImageHtml(bare) : url;
        });
    }).join('');
}

function _rewriteLocalImgSrc(html) {
    return html.replace(/<img\s([^>]*?)src="([^"]+)"([^>]*?)>/gi, (match, pre, src, post) => {
        const webSrc = _toWebUrl(src);
        const safeSrc = webSrc.replace(/"/g, '&quot;');
        const hasClick = /onclick/i.test(pre + post);
        const clickAttr = hasClick ? '' : ` onclick="_openImageLightbox(this.src)" style="cursor:zoom-in;"`;
        return `<img ${pre}src="${safeSrc}"${post}${clickAttr}>`;
    });
}

function renderMarkdown(text) {
    try {
        let html = md.render(text);
        html = _rewriteLocalImgSrc(html);
        // Order matters: video first (more specific), then image.
        html = injectImagePreviews(injectVideoPlayers(html));
        // Fallback for files the agent only mentions by path (workspace.js).
        if (typeof injectFileChips === 'function') html = injectFileChips(html);
        // Note: Code block headers are added via DOM manipulation after insertion
        // See addCodeBlockHeadersToElement()
        return html;
    }
    catch (e) { return text.replace(/\n/g, '<br>'); }
}

function _addCodeBlockHeaders(container) {
    // Add header with language label and copy button to each <pre> block using DOM manipulation
    const preBlocks = container.querySelectorAll('pre');
    preBlocks.forEach(pre => {
        if (pre.parentElement && pre.parentElement.classList.contains('code-block-wrapper')) return;
        
        const codeEl = pre.querySelector('code');
        if (!codeEl) return;
        
        const langClass = Array.from(codeEl.classList).find(c => c.startsWith('language-'));
        const language = langClass ? langClass.replace('language-', '') : '';
        // Hide label for unknown/empty languages (e.g. language-undefined)
        const showLang = language && language !== 'undefined' && language !== 'code';
        const langLabel = showLang ? language.charAt(0).toUpperCase() + language.slice(1) : '';
        
        const wrapper = document.createElement('div');
        wrapper.className = 'code-block-wrapper';
        
        const header = document.createElement('div');
        header.className = 'code-block-header';
        header.innerHTML = `
            <span class="code-block-lang">${langLabel}</span>
            <button class="code-copy-btn" title="Copy code">
                <i class="fas fa-copy"></i>
            </button>
        `;
        
        pre.parentNode.insertBefore(wrapper, pre);
        wrapper.appendChild(header);
        wrapper.appendChild(pre);
    });
}

// =====================================================================
// Mermaid Diagrams
// =====================================================================
// mermaid is fetched at runtime instead of being vendored in-tree (review
// decision on #3221): a version-pinned jsdelivr URL, verified by a
// subresource integrity hash so a tampered or silently-changed CDN build
// never runs in the console. The matching hash is registered in
// static/vendor/README.md. Consoles without internet access never load it
// and simply keep plain code blocks.
const MERMAID_CDN_URL = 'https://cdn.jsdelivr.net/npm/mermaid@11.17.2/dist/mermaid.min.js';
const MERMAID_SRI_HASH = 'sha384-EOXBFmc3gx5mb+vn0vPvvGqACToJD24hhacX5Yx+8NUUQrHIle/Qi5Bg9o3zKwW2';

let _mermaidLoadPromise = null;

function ensureMermaidLoaded() {
    // Lazily inject the IIFE build (~3.5MB) on the first ```mermaid block:
    // a conversation without diagrams never pays for it. Same pattern as
    // ensureD3Loaded(). The integrity hash pins the exact bytes of
    // mermaid@11.17.2; SRI needs CORS, hence crossOrigin.
    if (window.mermaid) return Promise.resolve(window.mermaid);
    if (_mermaidLoadPromise) return _mermaidLoadPromise;
    _mermaidLoadPromise = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = MERMAID_CDN_URL;
        script.integrity = MERMAID_SRI_HASH;
        script.crossOrigin = 'anonymous';
        script.async = true;
        script.onload = () => resolve(window.mermaid);
        script.onerror = () => {
            // Clear the memo so a transient failure can be retried on the
            // next applyHighlighting pass.
            _mermaidLoadPromise = null;
            reject(new Error('Failed to load mermaid'));
        };
        document.head.appendChild(script);
    });
    return _mermaidLoadPromise;
}

function _mermaidThemeName() {
    // theme.js toggles the `dark` class on <html>; mermaid's built-in themes
    // are named 'default' (light) and 'dark'.
    return document.documentElement.classList.contains('dark') ? 'dark' : 'default';
}

let _mermaidSeq = 0;

function _renderMermaidBlock(mermaid, block) {
    const pre = block.querySelector('pre');
    const codeEl = pre && pre.querySelector('code');
    const source = codeEl ? codeEl.textContent : '';
    const theme = _mermaidThemeName();
    const id = 'mermaid-svg-' + (++_mermaidSeq);  // render() needs a fresh id per call
    mermaid.initialize({
        startOnLoad: false,
        // Message content is untrusted agent output: 'strict' disables HTML
        // labels and click callbacks so a diagram cannot run script or call out.
        securityLevel: 'strict',
        theme: theme,
        // Parse failures must reject quietly; without this mermaid paints its
        // own error graphic into the page.
        suppressErrorRendering: true,
    });
    mermaid.render(id, source).then(({ svg }) => {
        // The node may have been replaced while a newer chunk streamed in,
        // or re-marked pending by a theme switch; a stale SVG must not land.
        if (!block.isConnected || block.dataset.mermaid !== 'rendering') return;
        if (_mermaidThemeName() !== theme) {
            block.dataset.mermaid = 'pending';
            renderMermaidBlocks(block.parentElement);
            return;
        }
        const figure = document.createElement('div');
        figure.className = 'mermaid-figure';
        figure.innerHTML = svg;
        pre.parentNode.insertBefore(figure, pre);
        // Keep the (hidden) source: the copy button in code view reads it,
        // and a theme switch re-renders the diagram from it.
        pre.classList.add('hidden');
        _installMermaidToolbar(block);
        block.dataset.mermaid = 'done';
    }).catch(() => {
        // Invalid or still-incomplete syntax (normal while a diagram streams
        // in): leave the plain code block, header and copy button included.
        if (block.isConnected && block.dataset.mermaid === 'rendering') {
            block.dataset.mermaid = 'invalid';
        }
    });
}

// ---------------------------------------------------------------------
// Mermaid Diagram View: chart/code toggle, zoom, fullscreen
//
// A rendered diagram gets the DeepSeek-style header controls (#3221
// review): a [Chart|Code] toggle plus zoom and fullscreen tools on the
// chart side. Chart is the default; the source <pre> always stays in the
// DOM, so code view always has something to show and copy.
// ---------------------------------------------------------------------
const MERMAID_ZOOM_MIN = 0.4;
const MERMAID_ZOOM_MAX = 3;

function _installMermaidToolbar(block) {
    const header = block.querySelector('.code-block-header');
    if (!header) return;
    // Chart is the default view; the marker lives on the block, which
    // survives a theme re-render, so the user's choice is kept across one.
    if (!block.dataset.mermaidMode) block.dataset.mermaidMode = 'chart';

    if (!header.querySelector('.mermaid-toolbar')) {
        const toolbar = document.createElement('div');
        toolbar.className = 'mermaid-toolbar';
        toolbar.innerHTML =
            '<button class="mermaid-mode-btn" data-mermaid-view="chart" title="Chart view">' +
                '<i class="fas fa-diagram-project"></i></button>' +
            '<button class="mermaid-mode-btn" data-mermaid-view="code" title="Code view">' +
                '<i class="fas fa-code"></i></button>' +
            '<span class="mermaid-chart-tools">' +
                '<button class="mermaid-tool-btn" data-mermaid-zoom="in" title="Zoom in">' +
                    '<i class="fas fa-magnifying-glass-plus"></i></button>' +
                '<button class="mermaid-tool-btn" data-mermaid-zoom="out" title="Zoom out">' +
                    '<i class="fas fa-magnifying-glass-minus"></i></button>' +
                '<button class="mermaid-tool-btn" data-mermaid-zoom="reset" title="Reset zoom">' +
                    '<i class="fas fa-arrows-rotate"></i></button>' +
                '<button class="mermaid-tool-btn mermaid-fullscreen-btn" title="Fullscreen">' +
                    '<i class="fas fa-expand"></i></button>' +
            '</span>';
        header.insertBefore(toolbar, header.querySelector('.code-copy-btn'));
        toolbar.addEventListener('click', (e) => {
            const modeBtn = e.target.closest('[data-mermaid-view]');
            if (modeBtn) {
                block.dataset.mermaidMode = modeBtn.dataset.mermaidView;
                _syncMermaidToolbar(block);
                return;
            }
            const zoomBtn = e.target.closest('[data-mermaid-zoom]');
            if (zoomBtn) {
                const current = parseFloat(block.dataset.mermaidZoomLevel || '1');
                const action = zoomBtn.dataset.mermaidZoom;
                _setMermaidZoom(block, action === 'in' ? current * 1.25
                    : action === 'out' ? current / 1.25 : 1);
                return;
            }
            if (e.target.closest('.mermaid-fullscreen-btn')) _toggleMermaidFullscreen(block);
        });
    }
    _syncMermaidToolbar(block);
    // A theme re-render swaps in a fresh SVG: re-apply the saved zoom to it.
    const savedZoom = parseFloat(block.dataset.mermaidZoomLevel || '1');
    if (savedZoom !== 1) _setMermaidZoom(block, savedZoom);
}

function _syncMermaidToolbar(block) {
    const mode = block.dataset.mermaidMode || 'chart';
    block.dataset.mermaidMode = mode;
    block.querySelectorAll('.mermaid-mode-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mermaidView === mode);
    });
}

// Zoom resizes the SVG by width (percent), not transform: the figure keeps
// its overflow-x scroller, so an oversized diagram scrolls instead of
// overflowing the bubble. The level lives on data-mermaid-zoom-level (not
// data-mermaid-zoom, which names the buttons — a matching attribute on the
// block would swallow the fullscreen button's closest() lookup).
function _setMermaidZoom(block, zoom) {
    zoom = Math.min(MERMAID_ZOOM_MAX, Math.max(MERMAID_ZOOM_MIN, zoom));
    block.dataset.mermaidZoomLevel = String(zoom);
    const svg = block.querySelector('.mermaid-figure svg');
    if (!svg) return;
    svg.style.width = (zoom * 100) + '%';
    svg.style.maxWidth = zoom > 1 ? 'none' : '';
}

function _toggleMermaidFullscreen(block) {
    const wrapper = block.querySelector('.code-block-wrapper');
    if (!wrapper) return;
    if (block.classList.contains('mermaid-fullscreen')) {
        block.classList.remove('mermaid-fullscreen');
        return;
    }
    if (document.fullscreenElement) {
        if (document.exitFullscreen) document.exitFullscreen();
        return;
    }
    if (wrapper.requestFullscreen) {
        wrapper.requestFullscreen().catch(() => _toggleMermaidFallbackFullscreen(block));
    } else {
        _toggleMermaidFallbackFullscreen(block);
    }
}

// Browsers without the Fullscreen API (or where it is refused): a fixed,
// full-viewport class on the block is the poor man's fullscreen. Esc or the
// same button leaves it, mirroring native behavior.
let _mermaidEscBound = false;

function _toggleMermaidFallbackFullscreen(block) {
    block.classList.toggle('mermaid-fullscreen');
    if (!block.classList.contains('mermaid-fullscreen') || _mermaidEscBound) return;
    _mermaidEscBound = true;
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        document.querySelectorAll('.mermaid-block.mermaid-fullscreen')
            .forEach(el => el.classList.remove('mermaid-fullscreen'));
    });
}

// The button icon must track reality even when the user leaves fullscreen
// with Esc (the browser exits without telling the toolbar).
function _syncMermaidFullscreenIcons() {
    const active = !!document.fullscreenElement;
    document.querySelectorAll('.mermaid-fullscreen-btn i').forEach(icon => {
        icon.className = active ? 'fas fa-compress' : 'fas fa-expand';
    });
}
document.addEventListener('fullscreenchange', _syncMermaidFullscreenIcons);

// Upgrade the ```mermaid placeholders emitted by the fence rule to real
// diagrams. Idempotent: only 'pending' blocks are picked up, each moving
// through 'rendering' to 'done' (or 'invalid' when mermaid rejects the
// source), so repeated applyHighlighting passes never re-render a finished
// diagram. Called with the bubble/container; document covers everything.
function renderMermaidBlocks(container) {
    const root = container || document;
    const blocks = root.querySelectorAll('.mermaid-block[data-mermaid="pending"]');
    if (blocks.length === 0) return;
    ensureMermaidLoaded().then((mermaid) => {
        blocks.forEach(block => {
            // Skip nodes a re-render swapped out while the script was loading.
            if (!block.isConnected || block.dataset.mermaid !== 'pending') return;
            block.dataset.mermaid = 'rendering';
            _renderMermaidBlock(mermaid, block);
        });
    }).catch(() => {
        // CDN unreachable (offline / firewalled console): fall back to plain
        // code blocks instead of placeholders that never upgrade.
        blocks.forEach(block => {
            if (block.isConnected) block.dataset.mermaid = 'invalid';
        });
    });
}

