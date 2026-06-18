document.addEventListener("DOMContentLoaded", () => {
  const refreshIcons = () => {
    if (window.lucide) window.lucide.createIcons();
  };
  refreshIcons();

  const modal = document.querySelector("[data-app-modal]");
  const modalTitle = modal.querySelector("[data-modal-title]");
  const modalSubtitle = modal.querySelector("[data-modal-subtitle]");
  const modalBody = modal.querySelector("[data-modal-body]");
  const modalConfirm = modal.querySelector("[data-modal-confirm]");
  const modalCancel = modal.querySelector("[data-modal-cancel]");
  let confirmAction = null;
  let modalTrigger = null;

  const closeModal = () => {
    modal.hidden = true;
    document.body.classList.remove("modal-open");
    confirmAction = null;
    if (modalTrigger) modalTrigger.focus();
    modalTrigger = null;
  };

  const openModal = ({ title, subtitle = "", body, trigger, confirmLabel = "" }) => {
    modalTitle.textContent = title;
    modalSubtitle.textContent = subtitle;
    modalSubtitle.hidden = !subtitle;
    modalBody.replaceChildren(body);
    modalConfirm.hidden = !confirmLabel;
    modalConfirm.textContent = confirmLabel;
    modalCancel.textContent = confirmLabel ? "Cancel" : "Close";
    modalTrigger = trigger;
    modal.hidden = false;
    document.body.classList.add("modal-open");
    (confirmLabel ? modalConfirm : modalCancel).focus();
    refreshIcons();
  };

  const messageBody = (message) => {
    const paragraph = document.createElement("p");
    paragraph.className = "modal-message";
    paragraph.textContent = message;
    return paragraph;
  };

  const showToast = (message) => {
    const toast = document.querySelector("#toast");
    toast.textContent = message;
    toast.classList.add("visible");
    window.clearTimeout(showToast.timeout);
    showToast.timeout = window.setTimeout(() => toast.classList.remove("visible"), 2200);
  };

  const copyText = async (value) => {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return;
    }
    const input = document.createElement("textarea");
    input.value = value;
    input.setAttribute("readonly", "");
    input.style.position = "fixed";
    input.style.opacity = "0";
    document.body.append(input);
    input.select();
    const copied = document.execCommand("copy");
    input.remove();
    if (!copied) throw new Error("Copy is not supported by this browser.");
  };

  const providerBody = (provider) => {
    const list = document.createElement("dl");
    list.className = "provider-summary";
    [
      ["Provider name", provider.name || "—", false, false],
      ["Status", provider.status || "pending", false, false],
      ["Compatibility", provider.compatibility || "unknown", false, false],
      ["Available models", String(provider.model_count ?? 0), false, false],
      ["Base URL", provider.base_url || "—", true, true],
      ["API key label", provider.api_key_label || "Default", false, false],
      ["API key", provider.api_key || "Public endpoint / empty", true, true],
      ["Notes", provider.notes || "No notes", false, false],
    ].forEach(([label, value, code, copyable]) => {
      const row = document.createElement("div");
      const term = document.createElement("dt");
      const detail = document.createElement("dd");
      term.textContent = label;
      if (copyable) {
        const valueElement = document.createElement("span");
        const copyButton = document.createElement("button");
        detail.className = "provider-summary-copy";
        valueElement.className = "provider-summary-code";
        valueElement.textContent = value;
        copyButton.className = "icon-button";
        copyButton.type = "button";
        copyButton.title = `Copy ${label}`;
        copyButton.dataset.copyValue = value;
        copyButton.innerHTML = '<i data-lucide="copy"></i>';
        detail.append(valueElement, copyButton);
      } else {
        detail.textContent = value;
        if (label === "Status" || label === "Compatibility") {
          detail.className = `provider-summary-state ${String(value).toLowerCase()}`;
        }
      }
      if (code && !copyable) detail.className = "provider-summary-code";
      row.append(term, detail);
      list.append(row);
    });
    return list;
  };

  modal.querySelectorAll("[data-modal-close], [data-modal-cancel]").forEach((button) => {
    button.addEventListener("click", closeModal);
  });
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeModal();
  });
  modalConfirm.addEventListener("click", () => {
    const action = confirmAction;
    closeModal();
    if (action) action();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.hidden) closeModal();
  });

  document.querySelectorAll(".reveal-secret").forEach((button) => {
    button.addEventListener("click", () => {
      const input = button.parentElement.querySelector("input");
      const isHidden = input.type === "password";
      input.type = isHidden ? "text" : "password";
      const showLabel = button.dataset.showLabel || "Show";
      const hideLabel = button.dataset.hideLabel || "Hide";
      button.innerHTML = `<i data-lucide="${isHidden ? "eye-off" : "eye"}"></i><span>${isHidden ? hideLabel : showLabel}</span>`;
      refreshIcons();
    });
  });

  document.addEventListener("click", async (event) => {
    const copyButton = event.target.closest("[data-copy-value]");
    if (copyButton) {
      copyButton.disabled = true;
      try {
        await copyText(copyButton.dataset.copyValue);
        copyButton.innerHTML = '<i data-lucide="check"></i>';
        showToast("Copied to clipboard");
      } catch (error) {
        showToast(error.message);
      } finally {
        window.setTimeout(() => {
          copyButton.disabled = false;
          copyButton.innerHTML = '<i data-lucide="copy"></i>';
          refreshIcons();
        }, 1200);
        refreshIcons();
      }
      return;
    }

    const revealAccountKey = event.target.closest("[data-reveal-account-key]");
    if (revealAccountKey) {
      const container = revealAccountKey.closest(".provider-credential-secret");
      const input = container.querySelector("[data-detail-api-key]");
      if (input.type === "text") {
        input.type = "password";
        input.value = "";
        revealAccountKey.innerHTML = '<i data-lucide="eye"></i><span>Show Apikey</span>';
        refreshIcons();
        return;
      }
      revealAccountKey.disabled = true;
      try {
        const response = await fetch(revealAccountKey.dataset.revealAccountKey, {
          headers: { Accept: "application/json" },
          cache: "no-store",
        });
        if (!response.ok) throw new Error("API key could not be loaded.");
        input.value = (await response.json()).api_key;
        input.type = "text";
        revealAccountKey.innerHTML = '<i data-lucide="eye-off"></i><span>Hide Apikey</span>';
      } catch (error) {
        showToast(error.message);
      } finally {
        revealAccountKey.disabled = false;
        refreshIcons();
      }
      return;
    }

    const viewButton = event.target.closest("[data-provider-summary-url]");
    if (!viewButton) return;
    viewButton.disabled = true;
    try {
      const response = await fetch(viewButton.dataset.providerSummaryUrl, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) throw new Error("Provider information could not be loaded.");
      const provider = await response.json();
      openModal({
        title: provider.name || "Provider information",
        subtitle: "Provider configuration",
        body: providerBody(provider),
        trigger: viewButton,
      });
    } catch (error) {
      openModal({
        title: "Unable to load provider",
        body: messageBody(error.message),
        trigger: viewButton,
      });
    } finally {
      viewButton.disabled = false;
    }
  });

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!form.dataset.confirmTitle || form.dataset.confirmBypass === "true") {
      if (form.dataset.confirmBypass === "true") delete form.dataset.confirmBypass;
      return;
    }
    event.preventDefault();
    const submitter = event.submitter;
    confirmAction = () => {
      form.dataset.confirmBypass = "true";
      form.requestSubmit(submitter);
    };
    openModal({
      title: form.dataset.confirmTitle,
      body: messageBody(form.dataset.confirmMessage || "Continue with this action?"),
      trigger: submitter,
      confirmLabel: form.dataset.confirmLabel || "Confirm",
    });
  }, true);

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
