(function () {
    'use strict';

    const GROUP_GAP_MS = 5 * 60 * 1000;
    const STICK_PX = 48;

    document.addEventListener('alpine:init', () => {
        Alpine.data('chatPanel', () => ({
            draft: '',
            asServer: true,
            unseen: 0,
            stick: true,
            max: 1000,
            observer: null,
            lastId: 0,
            firstId: 0,

            get remaining() {
                return this.max - this.draft.length;
            },
            get counter() {
                return this.draft.length ? String(this.remaining) : '';
            },
            get canSend() {
                return this.draft.trim().length > 0;
            },
            get placeholder() {
                return this.asServer ? 'Announce as Server…' : 'Write a message…';
            },
            get pillText() {
                return this.unseen === 1 ? '1 new message' : this.unseen + ' new messages';
            },

            init() {
                this.max = parseInt(this.$el.dataset.max) || this.max;
                this.decorate();
                this.syncIds();

                document.body.addEventListener('bsn-chat-refresh', () => {
                    this.draft = '';
                    this.$nextTick(() => this.autosize());
                    this.$refs.error.innerHTML = '';
                    this.stick = true;
                });

                this.observer = new MutationObserver(() => this.onListChange());
                this.observer.observe(this.$refs.list, {childList: true});

                new IntersectionObserver((entries, io) => {
                    if (entries.some(e => e.isIntersecting)) {
                        this.scrollToBottom();
                        io.disconnect();
                    }
                }).observe(this.$el);
            },

            messages() {
                return Array.from(this.$refs.list.querySelectorAll(':scope > .chat-msg'));
            },

            syncIds() {
                const msgs = this.messages();
                this.firstId = msgs.length ? parseInt(msgs[0].dataset.id) : 0;
                this.lastId = msgs.length ? parseInt(msgs[msgs.length - 1].dataset.id) : 0;
            },

            // Group consecutive messages and add day separators (client-side to be timezones aware)
            decorate() {
                const list = this.$refs.list;
                list.querySelectorAll(':scope > .chat-day').forEach(el => el.remove());
                if (this.messages().length) list.querySelectorAll(':scope > .chat-empty').forEach(el => el.remove());

                let prev = null;
                for (const el of this.messages()) {
                    const time = parseInt(el.dataset.time);
                    const day = new Date(time).toDateString();
                    const newDay = !prev || prev.day !== day;

                    if (newDay) {
                        const sep = document.createElement('div');
                        sep.className = 'chat-day';
                        sep.textContent = new Date(time).toLocaleDateString(undefined,
                            {weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'});
                        list.insertBefore(sep, el);
                    }

                    const system = el.classList.contains('chat-system') || el.classList.contains('chat-editing');
                    el.classList.toggle('chat-cont', !system && !newDay && !!prev && !prev.system
                        && prev.user === el.dataset.user && time - prev.time < GROUP_GAP_MS);

                    prev = {user: el.dataset.user, time, day, system};
                }
            },

            onListChange() {
                const scroll = this.$refs.scroll;
                const oldFirst = this.firstId;
                const oldLast = this.lastId;
                const heightBefore = scroll.scrollHeight;

                this.observer.disconnect();
                this.decorate();
                this.observer.observe(this.$refs.list, {childList: true});
                this.syncIds();

                if (oldFirst && this.firstId && this.firstId < oldFirst) {
                    scroll.scrollTop += scroll.scrollHeight - heightBefore;
                } else if (this.lastId > oldLast) {
                    if (this.stick) {
                        this.scrollToBottom();
                    } else {
                        this.unseen += this.messages().filter(el => parseInt(el.dataset.id) > oldLast).length;
                    }
                }
            },

            onScroll() {
                const s = this.$refs.scroll;
                this.stick = s.scrollHeight - s.scrollTop - s.clientHeight < STICK_PX;
                if (this.stick) this.unseen = 0;
            },

            scrollToBottom() {
                this.$nextTick(() => {
                    const s = this.$refs.scroll;
                    s.scrollTop = s.scrollHeight;
                    this.stick = true;
                    this.unseen = 0;
                });
            },

            autosize() {
                const input = this.$refs.input;
                input.style.height = 'auto';
                input.style.height = Math.min(input.scrollHeight, 128) + 'px';
            },

            // Enter to send, Shift+Enter for new line
            onEnter(event) {
                if (event.shiftKey || event.isComposing) return;
                event.preventDefault();
                if (this.canSend) this.$refs.form.requestSubmit();
            },
        }));
    });
})();
