(function () {
    'use strict';

    // YAML syntax highlighting and live validation for the beets config editor

    const escapeHtml = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const span = (cls, text) => text ? `<span class="yaml-${cls}">${escapeHtml(text)}</span>` : '';

    //  quoted strings swallow all '#' or ':' they contain
    const VALUE_TOKENS = new RegExp([
        /(?<str>"(?:[^"\\]|\\.)*"?|'(?:[^']|'')*'?)/,
        /(?<comment>(?:^|(?<=\s))#.*$)/,
        /(?<bool>\b(?:true|false|yes|no|on|off|null)\b|~)/i,
        /(?<num>-?\b\d+(?:\.\d+)?\b)/,
        /(?<anchor>[&*][\w-]+|!\S*)/,
        /(?<var>\$\w+|%\w+\{|\})/,      // beets path templates: $field, %function{...}
    ].map(r => r.source).join('|'), 'gi');

    const KEY = /^((?:"[^"]*"|'[^']*'|[^\s:#"'\-][^:#]*?|-[^\s:#][^:#]*?))(\s*)(:)(?=\s|$)(.*)$/;

    function highlightValue(text) {
        let out = '', pos = 0;
        for (const m of text.matchAll(VALUE_TOKENS)) {
            const [kind, token] = Object.entries(m.groups).find(([, v]) => v !== undefined);
            out += escapeHtml(text.slice(pos, m.index)) + span(kind, token);
            pos = m.index + token.length;
        }
        return out + escapeHtml(text.slice(pos));
    }

    function highlightLine(line) {
        if (/^(---|\.\.\.)\s*$/.test(line)) return span('punct', line);

        const [, indent, dashes, rest] = line.match(/^(\s*)((?:-\s+)*)(.*)$/);
        const key = rest.match(KEY);
        const body = key
            ? span('key', key[1]) + key[2] + span('punct', key[3]) + highlightValue(key[4])
            : highlightValue(rest);
        return indent + span('punct', dashes) + body;
    }

    function highlightYaml(text, errorLine, lockedLines) {
        return text.split('\n').map((line, i) => {
            const flag = i + 1 === errorLine ? ' yaml-line-error' : lockedLines.has(i + 1) ? ' yaml-line-locked' : '';
            return `<div class="yaml-line${flag}">${highlightLine(line)}</div>`;
        }).join('');
    }

    // Editor

    let errorLine = null;
    let lockedLines = new Set();
    let validateTimer = null;
    let validateSeq = 0;

    const editor = () => document.getElementById('beetsConfigEditor');

    function render() {
        const textarea = editor();
        const layer = document.getElementById('beetsConfigHighlight');
        if (!textarea || !layer) return;
        layer.innerHTML = highlightYaml(textarea.value, errorLine, lockedLines);

        const gutter = document.getElementById('beetsConfigGutter');
        const count = textarea.value.split('\n').length;
        gutter.textContent = Array.from({length: count}, (_, i) => i + 1).join('\n');

        syncScroll(textarea);
    }

    function syncScroll(textarea) {
        const layer = document.getElementById('beetsConfigHighlight');
        layer.scrollTop = textarea.scrollTop;
        layer.scrollLeft = textarea.scrollLeft;
        document.getElementById('beetsConfigGutter').scrollTop = textarea.scrollTop;
    }

    function showStatus(ok, message) {
        const status = document.getElementById('beetsConfigStatus');
        if (status) {
            status.textContent = message;
            status.classList.toggle('test-result-fail', !ok);
            status.hidden = ok;
        }
        const save = document.getElementById('beetsConfigSave');
        if (save && !editor().readOnly) save.disabled = !ok;
    }

    async function validate() {
        const textarea = editor();
        if (!textarea || textarea.readOnly) return;
        const seq = ++validateSeq;
        const csrfInput = document.querySelector('input[name="csrf_token"]');
        try {
            const resp = await fetch(textarea.dataset.validateUrl, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {'X-CSRFToken': csrfInput ? csrfInput.value : ''},
                body: new URLSearchParams({content: textarea.value}),
            });
            if (!resp.ok || seq !== validateSeq) return;     // server hiccup? keep what's shown
            const result = await resp.json();
            errorLine = result.ok ? null : result.line;
            lockedLines = new Set(result.locked);
            showStatus(result.ok, result.message);
            render();
        } catch (err) {
            // Offline? server checks again on save
        }
    }

    function refresh() {
        errorLine = null;
        render();
        validate();
    }

    document.addEventListener('input', event => {
        if (event.target.id !== 'beetsConfigEditor') return;
        render();
        clearTimeout(validateTimer);
        validateTimer = setTimeout(validate, 400);
    });

    document.addEventListener('scroll', event => {
        if (event.target.id === 'beetsConfigEditor') syncScroll(event.target);
    }, true);

    bsnCommon.onSwap('beetsConfigEditorWrap', refresh);   // after Save / Reload
    refresh();
})();
