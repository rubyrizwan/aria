document.addEventListener("DOMContentLoaded", () => {
  if (window.lucide) window.lucide.createIcons();

  document.querySelectorAll(".reveal-secret").forEach((button) => {
    button.addEventListener("click", () => {
      const input = button.parentElement.querySelector("input");
      const isHidden = input.type === "password";
      input.type = isHidden ? "text" : "password";
      const showLabel = button.dataset.showLabel || "Show";
      const hideLabel = button.dataset.hideLabel || "Hide";
      button.innerHTML = `<i data-lucide="${isHidden ? "eye-off" : "eye"}"></i><span>${isHidden ? hideLabel : showLabel}</span>`;
      if (window.lucide) window.lucide.createIcons();
    });
  });

  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (event.defaultPrevented) return;
      const button = event.submitter || form.querySelector('button[type="submit"], button:not([type])');
      if (!button || button.disabled) return;

      button.disabled = true;
      button.classList.add("is-loading");
      button.setAttribute("aria-busy", "true");
      const iconOnly = button.classList.contains("icon-button");
      const label = button.dataset.loadingLabel || "Processing...";
      button.innerHTML = iconOnly
        ? '<span class="loading-spinner" aria-hidden="true"></span>'
        : `<span class="loading-spinner" aria-hidden="true"></span><span>${label}</span>`;
    });
  });
});
