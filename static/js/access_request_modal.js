(() => {
  "use strict";

  const initializeAccessRequestModal = () => {
    const modal = document.getElementById("access-request-modal");
    if (!modal) return;

    const title = document.getElementById("access-request-title");
    const message = document.getElementById("access-request-message");
    const permission = document.getElementById("access-request-permission");
    const reason = document.getElementById("access-request-reason");
    const requestForm = document.getElementById("access-request-form");
    const requestPanel = document.getElementById("access-request-panel");
    const infoPanel = document.getElementById("access-request-info");
    const closeButtons = modal.querySelectorAll("[data-access-close]");
    const submitButton = modal.querySelector("[data-access-submit]");

    const close = () => {
      modal.hidden = true;
      modal.setAttribute("aria-hidden", "true");
      document.body.classList.remove("overflow-hidden");
      if (reason) reason.value = "";
    };

    const open = (trigger) => {
      const isRequestable = trigger.dataset.accessRequestable === "true";
      if (title) title.textContent = trigger.dataset.accessLabel || "Additional access";
      if (message) {
        message.textContent = trigger.dataset.accessMessage ||
          "This action needs administrator approval for your organization.";
      }
      if (permission) permission.value = trigger.dataset.accessPermission || "";
      if (requestPanel) requestPanel.hidden = !isRequestable;
      if (infoPanel) infoPanel.hidden = isRequestable;
      modal.hidden = false;
      modal.setAttribute("aria-hidden", "false");
      document.body.classList.add("overflow-hidden");
      if (isRequestable && reason) {
        window.setTimeout(() => reason.focus(), 0);
      }
    };

    document.querySelectorAll("[data-access-trigger]").forEach((trigger) => {
      trigger.addEventListener("click", () => open(trigger));
    });
    closeButtons.forEach((button) => button.addEventListener("click", close));
    modal.addEventListener("click", (event) => {
      if (event.target === modal) close();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !modal.hidden) close();
    });
    if (requestForm && submitButton) {
      requestForm.addEventListener("submit", () => {
        submitButton.disabled = true;
        submitButton.textContent = "Sending request...";
        submitButton.classList.add("cursor-wait", "opacity-70");
      });
    }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeAccessRequestModal);
  } else {
    initializeAccessRequestModal();
  }
})();
