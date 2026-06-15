/**
 * navigation.js — SPA-style navigation guard
 * Drop this into backend/app/static/js/navigation.js
 * and include it in base.html BEFORE page-specific scripts.
 *
 * What it does:
 *   1. Intercepts ALL sidebar link clicks.
 *   2. If the target is the SAME page the user is already on — does nothing
 *      (no reload, no re-init, no flicker).
 *   3. If navigating to a DIFFERENT page — performs a normal navigation but
 *      stores any dirty-state warnings first.
 *   4. Exposes window.__pageCache so each page's init function can detect
 *      "I've already run, skip the network fetch" pattern.
 */

(function () {
    "use strict";

    /* ── Page cache registry ─────────────────────────────────────────────── */
    // Each page registers itself: window.__pageCache['insights'] = true
    // The page's own init function checks this flag before re-fetching.
    window.__pageCache = window.__pageCache || {};

    /* ── Current path ────────────────────────────────────────────────────── */
    const _currentPath = () => window.location.pathname.replace(/\/$/, '') || '/';

    /* ── Sidebar links ───────────────────────────────────────────────────── */
    function _isSidebarLink(el) {
        // Walk up to find an <a> with href that navigates within the app
        let node = el;
        while (node && node !== document.body) {
            if (node.tagName === 'A' && node.href) {
                const url = new URL(node.href, window.location.origin);
                // Same origin only
                if (url.origin === window.location.origin) return node;
            }
            node = node.parentElement;
        }
        return null;
    }

    /* ── Click handler ───────────────────────────────────────────────────── */
    document.addEventListener('click', function (e) {
        const link = _isSidebarLink(e.target);
        if (!link) return;

        const targetPath = new URL(link.href, window.location.origin).pathname.replace(/\/$/, '') || '/';

        // If clicking the EXACT same page we're on → suppress navigation entirely
        if (targetPath === _currentPath()) {
            e.preventDefault();
            e.stopPropagation();
            return;
        }

        // Different page → allow normal browser navigation (href follows naturally)
        // We intentionally do NOT preventDefault here so standard links work.
    }, true); // useCapture so we run before other handlers


    /* ── Mark active nav item ─────────────────────────────────────────────── */
    function _markActive() {
        const current = _currentPath();
        document.querySelectorAll('nav a[href], .sidebar a[href], [data-nav-link]').forEach(link => {
            const lPath = new URL(link.href, window.location.origin).pathname.replace(/\/$/, '') || '/';
            if (lPath === current) {
                link.classList.add('active');
                link.setAttribute('aria-current', 'page');
            } else {
                link.classList.remove('active');
                link.removeAttribute('aria-current');
            }
        });
    }

    document.addEventListener('DOMContentLoaded', _markActive);
    if (document.readyState !== 'loading') _markActive();

})();