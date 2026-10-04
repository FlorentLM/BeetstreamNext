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
                return new Promise(resolve => {
                    this.confirmResolve = resolve;
                    this.message = message;
                    this.show('confirmModal');
                });
            },

            prompt(message, defaultValue) {
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
})();
