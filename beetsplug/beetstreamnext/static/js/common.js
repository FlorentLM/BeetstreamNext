(function () {
    'use strict';

    // Theme

    function applyTheme(theme) {
        if (theme === 'light') {
            document.documentElement.setAttribute('data-theme', 'light');
        } else {
            document.documentElement.removeAttribute('data-theme');
        }
        document.querySelectorAll('[data-action="toggle-theme"]').forEach(btn => {
            btn.setAttribute('aria-pressed', theme === 'light' ? 'true' : 'false');
        });
    }

    function toggleTheme() {
        const current = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
        const next = current === 'light' ? 'dark' : 'light';
        try {
            document.cookie = 'bsn-theme=' + next + '; Path=/; Max-Age=31536000; SameSite=Lax';
        } catch (e) {
        }
        applyTheme(next);
    }

    applyTheme(document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark');

    // Clipboard

    function copySel(text) {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        let ok = false;
        try {
            ok = document.execCommand('copy');
        } catch (e) {
            // ignore
        }
        document.body.removeChild(ta);
        return ok;
    }

    // Copies text then calls done() on success
    function copyText(text, done) {
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(text).then(done).catch(() => {
                if (copySel(text)) done();
            });
        } else {
            // No Clipboard API over plain HTTP, fallback to execCommand
            if (copySel(text)) done();
        }
    }

    // One-time API key copy

    function copyApiKey(button) {
        const el = document.getElementById('apiKeyValue');
        if (!el) return;

        copyText(el.textContent.trim(), () => {
            button.textContent = 'Copied';
            setTimeout(() => {
                button.textContent = 'Copy';
            }, 2000);
        });
    }

    // Epoch timestamps (chat, podcast episodes, shares) in htmx-loaded partials

    function formatTimes(root = document) {
        root.querySelectorAll('.chat-time').forEach(el => {
            const ms = parseInt(el.dataset.timestamp);
            if (!isNaN(ms)) el.textContent = new Date(ms).toLocaleString();
        });
    }

    formatTimes();

    document.body.addEventListener('htmx:after:swap', event => {
        formatTimes(event.detail.ctx.target || document);
    });

    // htmx requests are same-origin -> attach the CSRF token to every one
    document.body.addEventListener('htmx:config:request', event => {
        const csrfInput = document.querySelector('input[name="csrf_token"]');
        if (csrfInput) event.detail.ctx.request.headers['X-CSRFToken'] = csrfInput.value;
    });

    // htmx's `keyup[key=='Enter']` trigger filters compile the condition with `new Function()`,
    // which our CSP blocks (no 'unsafe-eval') so enter-to-search is defined here
    document.addEventListener('keydown', event => {
        if (event.key !== 'Enter') return;
        const input = event.target.closest('#podcastDiscoveryQuery, #radioDiscoveryQuery');
        if (!input) return;
        event.preventDefault();
        const button = input.parentElement.querySelector('button[hx-get]');
        if (button) button.click();
    });

    document.addEventListener('change', event => {
        if (event.target.id === 'podcastOpmlFile' && event.target.files.length) {
            event.target.form.submit();
        }
    });

    document.addEventListener('click', event => {
        const target = event.target.closest('[data-action]');
        if (!target) return;

        switch (target.dataset.action) {
            case 'toggle-theme':
                toggleTheme();
                break;
            case 'copy-api-key':
                copyApiKey(target);
                break;
            case 'pick-file': {
                const fileInput = document.getElementById(target.dataset.target);
                if (fileInput) fileInput.click();
                break;
            }
        }
    });

    window.bsnCommon = {copyText};
})();
