(function () {
    'use strict';

    // Ffeedback for new password fields
    const MIN_LENGTH = 8;

    const RULES = {
        length: p => p.length >= MIN_LENGTH,
        lower: p => /\p{Ll}/u.test(p),
        upper: p => /\p{Lu}/u.test(p),
        digit: p => /\p{Nd}/u.test(p),
        special: p => /[^\p{L}\p{N}\s]/u.test(p),
    };

    function update(input) {
        const list = input.parentElement.querySelector('.password-rules');
        if (!list) return;

        list.classList.toggle('active', input.value !== '');
        list.querySelectorAll('li[data-rule]').forEach(li => {
            const test = RULES[li.dataset.rule];
            li.classList.toggle('met', !!test && test(input.value));
        });
    }

    document.addEventListener('input', event => {
        if (event.target.matches('input[data-password-rules]')) update(event.target);
    });

    // Reset buttons / htmx re-renders don't fire 'input'
    document.addEventListener('reset', event => {
        setTimeout(() => event.target.querySelectorAll('input[data-password-rules]').forEach(update));
    });
})();
