(function () {
    'use strict';

    // Modal store + confirm dialogs
    // (registered on 'alpine:init' so they exist before CSP build parses x-data)

    document.addEventListener('alpine:init', () => {
        Alpine.store('modal', {
            current: null,
            message: '',
            promptValue: '',
            confirmResolve: null,
            promptResolve: null,

            show(id) {
                this.current = id;
            },

            hide(id) {
                if (id && this.current !== id) return;
                this.current = null;
                this.settleConfirm(false);
                this.settlePrompt(null);
            },

            settleConfirm(result) {
                if (!this.confirmResolve) return;
                const resolve = this.confirmResolve;
                this.confirmResolve = null;
                resolve(result);
            },

            settlePrompt(result) {
                if (!this.promptResolve) return;
                const resolve = this.promptResolve;
                this.promptResolve = null;
                resolve(result);
            },

            confirm(message) {
                this.settleConfirm(false);
                return new Promise(resolve => {
                    this.confirmResolve = resolve;
                    this.message = message;
                    this.show('confirmModal');
                });
            },

            prompt(message, defaultValue) {
                this.settlePrompt(null);
                return new Promise(resolve => {
                    this.promptResolve = resolve;
                    this.message = message;
                    this.promptValue = defaultValue || '';
                    this.show('promptModal');
                    setTimeout(() => {
                        const input = document.getElementById('promptInput');
                        if (input) {
                            input.focus();
                            input.select();
                        }
                    }, 50);
                });
            },

            confirmProceed() {
                this.settleConfirm(true);
                this.hide('confirmModal');
            },

            promptProceed() {
                this.settlePrompt(this.promptValue);
                this.hide('promptModal');
            }
        });

        // Routing modal state via getters bcause the CSP build doesn't resolve the nested $store
        Alpine.data('modalPanel', (id) => ({
            get isOpen() {
                return Alpine.store('modal').current === id;
            },
            get message() {
                return Alpine.store('modal').message;
            },
            get promptValue() {
                return Alpine.store('modal').promptValue;
            },
            set promptValue(v) {
                Alpine.store('modal').promptValue = v;
            },
            open() {
                Alpine.store('modal').show(id);
            },
            close() {
                Alpine.store('modal').hide(id);
            },
            confirmProceed() {
                Alpine.store('modal').confirmProceed();
            },
            promptProceed() {
                Alpine.store('modal').promptProceed();
            }
        }));
    });

    // Confirm dialogs
    document.addEventListener('submit', event => {
        const form = event.target.closest('form[data-confirm]');
        if (!form) return;
        event.preventDefault();
        Alpine.store('modal').confirm(form.dataset.confirm).then(ok => {
            if (!ok) return;
            if (form.hasAttribute('hx-post')) htmx.trigger(form, 'confirmed');
            else form.submit();
        });
    });

    // hx-confirm use the modal
    document.body.addEventListener('htmx:confirm', event => {
        event.preventDefault();
        Alpine.store('modal').confirm(event.detail.ctx.confirm).then(ok => {
            if (ok) event.detail.issueRequest();
            else event.detail.dropRequest();
        });
    });

    // ARIA roles, focus move/trap/restore, Escape to close
    const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), ' +
        'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    const opened = new WeakSet();
    const returnFocus = new WeakMap();

    function focusables(root) {
        return Array.from(root.querySelectorAll(FOCUSABLE)).filter(el => el.getClientRects().length);
    }

    function describe(overlay, content) {
        content.setAttribute('role', overlay.classList.contains('modal-confirm') ? 'alertdialog' : 'dialog');
        content.setAttribute('aria-modal', 'true');
        content.tabIndex = -1;
        const heading = content.querySelector('h1, h2, h3');
        if (heading) {
            if (!heading.id) heading.id = (overlay.id || 'modal') + 'Title';
            content.setAttribute('aria-labelledby', heading.id);
        } else {
            content.setAttribute('aria-label', 'Dialog');
        }
        const text = content.querySelector('.confirm-text');
        if (text) {
            if (!text.id) text.id = (overlay.id || 'modal') + 'Text';
            content.setAttribute('aria-describedby', text.id);
        }
    }

    function onOpen(overlay) {
        const content = overlay.querySelector('.modal-content');
        if (!content) return;
        returnFocus.set(overlay, document.activeElement);
        describe(overlay, content);

        requestAnimationFrame(() => {
            const items = focusables(content);
            const target = items.find(el => el.matches('input, select, textarea'))
                || items.find(el => !el.classList.contains('close-modal'))
                || items[0] || content;
            target.focus();
        });
    }

    function onClose(overlay) {
        const el = returnFocus.get(overlay);
        returnFocus.delete(overlay);
        if (el && el.isConnected && typeof el.focus === 'function') el.focus();
    }

    function syncOverlay(overlay) {
        const active = overlay.classList.contains('active');
        if (active && !opened.has(overlay)) {
            opened.add(overlay);
            onOpen(overlay);
        } else if (!active && opened.has(overlay)) {
            opened.delete(overlay);
            onClose(overlay);
        }
    }

    function initDialogs() {
        document.querySelectorAll('.modal-overlay').forEach(syncOverlay);
        new MutationObserver(muts => {
            for (const m of muts) {
                if (m.type === 'attributes') {
                    if (m.target.classList.contains('modal-overlay')) syncOverlay(m.target);
                } else {
                    m.addedNodes.forEach(n => {
                        if (n.nodeType !== 1) return;
                        if (n.classList.contains('modal-overlay')) syncOverlay(n);
                        n.querySelectorAll('.modal-overlay').forEach(syncOverlay);
                    });
                }
            }
        }).observe(document.body, {attributes: true, attributeFilter: ['class'], childList: true, subtree: true});
    }

    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape' && event.key !== 'Tab') return;
        const overlays = document.querySelectorAll('.modal-overlay.active');
        if (!overlays.length) return;
        const overlay = overlays[overlays.length - 1];
        const content = overlay.querySelector('.modal-content');
        if (!content) return;

        if (event.key === 'Escape') {
            event.preventDefault();
            const store = Alpine.store('modal');
            if (store.current === overlay.id) store.hide(overlay.id);
            else overlay.querySelector('.close-modal')?.click();
            return;
        }

        const items = focusables(content);
        if (!items.length) {
            event.preventDefault();
            content.focus();
            return;
        }
        const first = items[0];
        const last = items[items.length - 1];
        const current = document.activeElement;
        if (!content.contains(current)) {
            event.preventDefault();
            first.focus();
        } else if (event.shiftKey && (current === first || current === content)) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && current === last) {
            event.preventDefault();
            first.focus();
        }
    });

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initDialogs);
    else initDialogs();
})();
