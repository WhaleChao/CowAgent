/* Light/dark theme toggle.
   Split out of console.js. These are classic scripts sharing one global
   scope; see channel/web/README.md before changing the load order. */

// =====================================================================
// Theme
// =====================================================================
let currentTheme = localStorage.getItem('cow_theme') || 'dark';

function applyTheme() {
    const root = document.documentElement;
    if (currentTheme === 'dark') {
        root.classList.add('dark');
        document.getElementById('theme-icon').className = 'fas fa-sun';
        document.getElementById('hljs-light').disabled = true;
        document.getElementById('hljs-dark').disabled = false;
    } else {
        root.classList.remove('dark');
        document.getElementById('theme-icon').className = 'fas fa-moon';
        document.getElementById('hljs-light').disabled = false;
        document.getElementById('hljs-dark').disabled = true;
    }
    rerenderMermaidDiagrams();
}

// Mermaid bakes the theme colors into the rendered SVG, so a theme switch
// must re-render every finished diagram. Flip the done markers back to
// pending, un-hide the code blocks (a slow re-render then shows source
// rather than a stale diagram) and let renderMermaidBlocks() pick them up.
// No-op at boot, where nothing has been rendered yet.
function rerenderMermaidDiagrams() {
    document.querySelectorAll('.mermaid-block[data-mermaid="done"]').forEach(block => {
        block.dataset.mermaid = 'pending';
        const figure = block.querySelector('.mermaid-figure');
        if (figure) figure.remove();
        const pre = block.querySelector('pre');
        if (pre) pre.classList.remove('hidden');
    });
    if (document.querySelector('.mermaid-block[data-mermaid="pending"]')) {
        renderMermaidBlocks(document);
    }
}

function toggleTheme() {
    currentTheme = currentTheme === 'dark' ? 'light' : 'dark';
    localStorage.setItem('cow_theme', currentTheme);
    applyTheme();
}

