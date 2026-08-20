(function () {
  "use strict";

  function initNavigation() {
    const nav = document.querySelector("[data-mobile-nav]");
    const backdrop = document.querySelector("[data-mobile-backdrop]");
    const openButton = document.querySelector("[data-mobile-open]");
    const closeButton = document.querySelector("[data-mobile-close]");
    if (!nav || !backdrop || !openButton) return;

    let lastFocused = null;

    function setOpen(open) {
      nav.classList.toggle("translate-x-0", open);
      nav.classList.toggle("-translate-x-full", !open);
      backdrop.hidden = !open;
      document.body.classList.toggle("overflow-hidden", open);
      openButton.setAttribute("aria-expanded", String(open));
      if (open) {
        lastFocused = document.activeElement;
        closeButton?.focus();
      } else {
        lastFocused?.focus?.();
      }
    }

    openButton.setAttribute("aria-expanded", "false");
    openButton.addEventListener("click", () => setOpen(true));
    closeButton?.addEventListener("click", () => setOpen(false));
    backdrop.addEventListener("click", () => setOpen(false));
    nav.querySelectorAll("[data-mobile-link]").forEach((link) => {
      link.addEventListener("click", () => setOpen(false));
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") setOpen(false);
    });
    window.addEventListener("resize", () => {
      if (window.matchMedia("(min-width: 1024px)").matches) setOpen(false);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initNavigation);
  } else {
    initNavigation();
  }
}());
