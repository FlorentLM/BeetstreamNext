(function () {
    'use strict';

    // Modal forms swap the list behind them on success
    document.body.addEventListener('htmx:after:swap', event => {
        const target = event.detail.ctx.target;
        if (target?.id !== 'radioList' || !target.querySelector('.flash-success')) return;
        const store = Alpine.store('modal');
        for (const [modalId, formId] of [['createRadioModal', 'createRadioForm'], ['editRadioModal', null]]) {
            if (store.current !== modalId) continue;
            const form = formId && document.getElementById(formId);
            if (form) {
                form.reset();
                form.querySelector('.modal-result').innerHTML = '';
            }
            store.hide(modalId);
        }
    });

    // Edit form
    document.body.addEventListener('htmx:after:swap', event => {
        if (event.detail.ctx.target?.id === 'editRadioModalBody') Alpine.store('modal').show('editRadioModal');
    });

    async function useRadioResult(target) {
        const nameInput = document.getElementById('createRadioName');
        const streamInput = document.getElementById('createRadioStreamUrl');
        const homepageInput = document.getElementById('createRadioHomepageUrl');
        if (nameInput) nameInput.value = target.dataset.name || '';
        if (streamInput) streamInput.value = target.dataset.streamUrl || '';
        if (homepageInput) homepageInput.value = target.dataset.homepageUrl || '';

        const favicon = target.dataset.favicon || '';
        const faviconInput = document.getElementById('createRadioFavicon');

        // Kept for create_station() in case the icon fetch fails client-side
        if (faviconInput) faviconInput.value = favicon;

        const results = document.getElementById('radioDiscoveryResults');
        if (results) results.innerHTML = '';

        const sendIcon = blob => window.dispatchEvent(new CustomEvent('radio-icon', {detail: {blob}}));
        sendIcon(null);

        const searchButton = document.querySelector('[data-action="discover-radios"]');
        const proxyBase = searchButton ? searchButton.dataset.faviconProxy : '';
        const name = target.dataset.name || '';
        const homepage = target.dataset.homepageUrl || '';
        if (!proxyBase || !name) return;

        // Fetch resolved icon here so it can be used in create_station()
        const params = new URLSearchParams({name});
        if (favicon) params.set('url', favicon);
        if (homepage) params.set('homepage', homepage);

        try {
            const resp = await fetch(`${proxyBase}?${params.toString()}`, {credentials: 'same-origin'});
            if (!resp.ok) return;
            sendIcon(await resp.blob());
        } catch (err) {
            // empty, favicon_url passed to create_station() to try server-side
        }
    }

    document.addEventListener('click', event => {
        const target = event.target.closest('[data-action="use-radio-result"]');
        if (target) useRadioResult(target);
    });
})();
