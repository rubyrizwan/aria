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
  const inferenceOverlay = document.querySelector("[data-inference-overlay]");
  let confirmAction = null;
  let modalTrigger = null;

  const closeModal = () => {
    modal.hidden = true;
    modal.querySelector(".app-modal").classList.remove("wide");
    document.body.classList.remove("modal-open");
    confirmAction = null;
    if (modalTrigger) modalTrigger.focus();
    modalTrigger = null;
  };

  const openModal = ({ title, subtitle = "", body, trigger, confirmLabel = "", wide = false }) => {
    modalTitle.textContent = title;
    modalSubtitle.textContent = subtitle;
    modalSubtitle.hidden = !subtitle;
    modalBody.replaceChildren(body);
    modalConfirm.hidden = !confirmLabel;
    modalConfirm.textContent = confirmLabel;
    modalCancel.textContent = confirmLabel ? "Cancel" : "Close";
    modal.querySelector(".app-modal").classList.toggle("wide", wide);
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

  const restartApplication = async (form, submitter) => {
    const body = document.createElement("div");
    body.className = "modal-message";
    body.innerHTML = '<span class="loading-spinner"></span><p>Restarting the server. This page will reconnect automatically.</p>';
    openModal({
      title: "Restarting API Checker",
      subtitle: `${window.location.hostname}:${window.location.port || (window.location.protocol === "https:" ? "443" : "80")}`,
      body,
      trigger: submitter,
    });
    modalCancel.disabled = true;
    modalCancel.textContent = "Restarting...";

    try {
      const response = await fetch(form.action, {
        method: "POST",
        headers: { Accept: "application/json" },
        body: new FormData(form),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "Application restart could not be started.");
      }

      await new Promise((resolve) => window.setTimeout(resolve, 1800));
      for (let attempt = 0; attempt < 30; attempt += 1) {
        try {
          const health = await fetch("/healthz", {
            headers: { Accept: "application/json" },
            cache: "no-store",
          });
          if (health.ok) {
            window.location.reload();
            return;
          }
        } catch (_) {
          // The connection is expected to fail while the server is restarting.
        }
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
      }
      throw new Error("The server did not become available within 30 seconds.");
    } catch (error) {
      body.replaceChildren(messageBody(error.message));
      modalTitle.textContent = "Restart failed";
      modalCancel.disabled = false;
      modalCancel.textContent = "Close";
      refreshIcons();
    }
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
    const container = document.createElement("div");
    container.className = "provider-modal-content";
    const list = document.createElement("dl");
    list.className = "provider-summary";
    const inference = provider.inference_summary || {};
    [
      ["Provider name", provider.name || "—", false, false],
      ["Status", provider.status || "pending", false, false],
      ["Monitoring", provider.monitoring || "disabled", false, false],
      ["Compatibility", provider.compatibility || "unknown", false, false],
      ["Available models", String(provider.model_count ?? 0), false, false],
      ["Inference available", String(inference.available ?? 0), false, false],
      ["Inference failed", String((inference.failed ?? 0) + (inference.forbidden ?? 0) + (inference.unauthorized ?? 0)), false, false],
      ["Quota exceeded", String(inference.quota_exceeded ?? 0), false, false],
      ["Last latency", provider.last_latency_ms == null ? "—" : `${Math.round(provider.last_latency_ms)} ms`, false, false],
      ["Last checked", provider.last_checked || "Never", false, false],
      ["Check interval", `${provider.interval_minutes ?? 60} minutes`, false, false],
      ["Base URL", provider.base_url || "—", true, true],
      ["API key label", provider.api_key_label || "Default", false, false],
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
        if (label === "Status" || label === "Compatibility" || label === "Monitoring") {
          detail.className = `provider-summary-state ${String(value).toLowerCase()}`;
        }
      }
      if (code && !copyable) detail.className = "provider-summary-code";
      row.append(term, detail);
      list.append(row);
    });

    const keyRow = document.createElement("div");
    const keyTerm = document.createElement("dt");
    const keyDetail = document.createElement("dd");
    const keyValue = document.createElement("span");
    const revealButton = document.createElement("button");
    const copyButton = document.createElement("button");
    const secret = provider.api_key || "";
    keyTerm.textContent = "API key";
    keyDetail.className = "provider-summary-copy";
    keyValue.className = "provider-summary-code";
    keyValue.textContent = maskedSecret(secret);
    keyValue.dataset.maskedSecret = "true";
    keyValue.dataset.secret = secret;
    revealButton.className = "icon-button";
    revealButton.type = "button";
    revealButton.title = "Show API key";
    revealButton.dataset.toggleModelSecret = "true";
    revealButton.innerHTML = '<i data-lucide="eye"></i>';
    copyButton.className = "icon-button";
    copyButton.type = "button";
    copyButton.title = "Copy API key";
    copyButton.dataset.copyValue = secret;
    copyButton.innerHTML = '<i data-lucide="copy"></i>';
    keyDetail.append(keyValue, revealButton, copyButton);
    keyRow.append(keyTerm, keyDetail);
    list.append(keyRow);

    const openProvider = document.createElement("a");
    openProvider.className = "button primary";
    openProvider.href = `/accounts/${provider.id}`;
    openProvider.innerHTML = '<i data-lucide="external-link"></i>Open provider details';
    container.append(list, openProvider);
    return container;
  };

  const maskedSecret = (secret) => {
    if (!secret) return "Public endpoint / empty";
    if (secret.length <= 8) return "•".repeat(secret.length);
    return `${secret.slice(0, 4)}${"•".repeat(Math.min(18, secret.length - 8))}${secret.slice(-4)}`;
  };

  const providerActionsBody = ({ id, name, modelCount }) => {
    const actions = document.createElement("div");
    actions.className = "provider-action-list";
    const open = document.createElement("a");
    const edit = document.createElement("a");
    open.href = `/accounts/${id}`;
    open.innerHTML = '<i data-lucide="external-link"></i><span><strong>Open details</strong><small>View models, credentials, and check history.</small></span>';
    edit.href = `/accounts/${id}/edit`;
    edit.innerHTML = '<i data-lucide="pencil"></i><span><strong>Edit provider</strong><small>Update endpoint, API key, notes, or schedule.</small></span>';
    actions.append(open, edit);
    if (modelCount > 0) {
      const testForm = document.createElement("form");
      const testButton = document.createElement("button");
      testForm.method = "post";
      testForm.action = `/accounts/${id}/test-models`;
      testForm.dataset.modelTestForm = "";
      testForm.dataset.confirmTitle = `Test access to ${modelCount} models?`;
      testForm.dataset.confirmMessage = "This sends minimal inference requests and may use provider quota or credit.";
      testForm.dataset.confirmLabel = "Run tests";
      testButton.type = "submit";
      testButton.innerHTML = '<i data-lucide="flask-conical"></i><span><strong>Test model access</strong><small>Run inference checks for every discovered model.</small></span>';
      testForm.append(testButton);
      actions.append(testForm);
    }
    const deleteForm = document.createElement("form");
    const deleteButton = document.createElement("button");
    deleteForm.method = "post";
    deleteForm.action = `/accounts/${id}/delete`;
    deleteForm.dataset.confirmTitle = `Delete ${name}?`;
    deleteForm.dataset.confirmMessage = "This provider and all stored check history will be permanently deleted.";
    deleteForm.dataset.confirmLabel = "Delete provider";
    deleteButton.type = "submit";
    deleteButton.className = "danger-item";
    deleteButton.innerHTML = '<i data-lucide="trash-2"></i><span><strong>Delete provider</strong><small>Permanently remove this provider and its data.</small></span>';
    deleteForm.append(deleteButton);
    actions.append(deleteForm);
    return actions;
  };

  const modelDetailBody = (model) => {
    const container = document.createElement("div");
    container.className = "model-detail-content";
    const toolbar = document.createElement("div");
    toolbar.className = "model-detail-toolbar";
    const summary = document.createElement("span");
    const copyModel = document.createElement("button");
    const copyConfig = document.createElement("button");
    summary.textContent = `${model.providers.length} providers · sorted by latency`;
    copyModel.className = "button secondary";
    copyModel.type = "button";
    copyModel.dataset.copyValue = model.model_id;
    copyModel.innerHTML = '<i data-lucide="copy"></i>Copy model ID';
    copyConfig.className = "button secondary";
    copyConfig.type = "button";
    copyConfig.dataset.copyValue = JSON.stringify(model.openai_config, null, 2);
    copyConfig.innerHTML = '<i data-lucide="braces"></i>Copy config';
    toolbar.append(summary, copyModel, copyConfig);

    const wrap = document.createElement("div");
    wrap.className = "table-wrap model-detail-table";
    const table = document.createElement("table");
    const head = document.createElement("thead");
    head.innerHTML = "<tr><th>Provider</th><th>Type</th><th>Status</th><th>Latency</th><th>HTTP</th><th>Last tested</th><th>Base URL</th><th>API key</th><th></th></tr>";
    const body = document.createElement("tbody");
    model.providers.forEach((provider) => {
      const row = document.createElement("tr");
      const providerCell = document.createElement("td");
      const typeCell = document.createElement("td");
      const statusCell = document.createElement("td");
      const latencyCell = document.createElement("td");
      const httpCell = document.createElement("td");
      const testedCell = document.createElement("td");
      const baseCell = document.createElement("td");
      const keyCell = document.createElement("td");
      const actionCell = document.createElement("td");
      const providerLink = document.createElement("a");
      const providerName = document.createElement("strong");
      const providerLabel = document.createElement("small");
      const providerNote = document.createElement("small");
      providerName.textContent = provider.provider;
      providerLabel.textContent = provider.api_key_label || "Default";
      providerCell.append(providerName, providerLabel);
      if (provider.notes) {
        providerNote.className = "model-provider-note";
        providerNote.textContent = provider.notes;
        providerCell.append(providerNote);
      }
      typeCell.innerHTML = `<span class="provider-summary-state ${provider.compatibility}">${provider.compatibility}</span>`;
      statusCell.innerHTML = `<span class="provider-summary-state ${provider.provider_status}">${provider.provider_status}</span>`;
      latencyCell.innerHTML = `<strong>${provider.latency_ms == null ? "—" : `${Math.round(provider.latency_ms)} ms`}</strong>`;
      httpCell.textContent = provider.http_status == null ? "—" : String(provider.http_status);
      testedCell.textContent = provider.last_tested || "Never";

      const baseValue = document.createElement("code");
      const baseCopy = document.createElement("button");
      baseCell.className = "model-detail-copy";
      baseValue.textContent = provider.base_url || "—";
      baseCopy.className = "icon-button";
      baseCopy.type = "button";
      baseCopy.title = "Copy Base URL";
      baseCopy.dataset.copyValue = provider.base_url || "";
      baseCopy.innerHTML = '<i data-lucide="copy"></i>';
      baseCell.append(baseValue, baseCopy);

      const keyValue = document.createElement("code");
      const revealButton = document.createElement("button");
      const keyCopy = document.createElement("button");
      const secret = provider.api_key || "";
      keyCell.className = "model-detail-copy";
      keyValue.textContent = maskedSecret(secret);
      keyValue.dataset.maskedSecret = "true";
      keyValue.dataset.secret = secret;
      revealButton.className = "icon-button";
      revealButton.type = "button";
      revealButton.title = "Show API key";
      revealButton.dataset.toggleModelSecret = "true";
      revealButton.innerHTML = '<i data-lucide="eye"></i>';
      keyCopy.className = "icon-button";
      keyCopy.type = "button";
      keyCopy.title = "Copy API key";
      keyCopy.dataset.copyValue = secret;
      keyCopy.innerHTML = '<i data-lucide="copy"></i>';
      keyCell.append(keyValue, revealButton, keyCopy);

      providerLink.className = "icon-button";
      providerLink.href = `/accounts/${provider.provider_id}`;
      providerLink.title = "Open provider";
      providerLink.innerHTML = '<i data-lucide="external-link"></i>';
      actionCell.append(providerLink);
      row.append(providerCell, typeCell, statusCell, latencyCell, httpCell, testedCell, baseCell, keyCell, actionCell);
      body.append(row);
    });
    table.append(head, body);
    wrap.append(table);
    container.append(toolbar, wrap);
    return container;
  };

  const startModelTest = async (form, trigger) => {
    const progressText = inferenceOverlay.querySelector("[data-inference-progress-text]");
    const progressBar = inferenceOverlay.querySelector("[data-inference-progress-bar]");
    const summary = inferenceOverlay.querySelector("[data-inference-summary]");
    const log = inferenceOverlay.querySelector("[data-inference-log]");
    const finishButton = inferenceOverlay.querySelector("[data-inference-finish]");
    let renderedLogs = 0;
    progressText.textContent = "Starting inference job...";
    progressBar.style.width = "0%";
    summary.replaceChildren();
    log.replaceChildren();
    finishButton.hidden = true;
    inferenceOverlay.hidden = false;
    document.body.classList.add("modal-open");
    trigger.disabled = true;

    const renderSummary = (counts) => {
      summary.replaceChildren();
      Object.entries(counts).forEach(([status, count]) => {
        const item = document.createElement("span");
        item.className = `inference-summary-item ${status}`;
        item.textContent = `${status.replaceAll("_", " ")}: ${count}`;
        summary.append(item);
      });
    };

    const renderLogs = (logs) => {
      logs.slice(renderedLogs).forEach((entry) => {
        const row = document.createElement("div");
        const status = document.createElement("span");
        const copy = document.createElement("span");
        row.className = "inference-log-row";
        status.className = `inference-log-status ${entry.status}`;
        status.textContent = entry.status.replaceAll("_", " ");
        copy.textContent = `${entry.model} · ${entry.message}`;
        row.append(status, copy);
        log.append(row);
      });
      renderedLogs = logs.length;
      log.scrollTop = log.scrollHeight;
    };

    try {
      const response = await fetch(form.action, {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "Inference job could not be started.");
      }
      const started = await response.json();
      while (true) {
        const progressResponse = await fetch(started.progress_url, {
          headers: { Accept: "application/json" },
          cache: "no-store",
        });
        if (!progressResponse.ok) throw new Error("Inference progress was lost.");
        const job = await progressResponse.json();
        const percent = job.total ? Math.round((job.completed / job.total) * 100) : 0;
        progressBar.style.width = `${percent}%`;
        progressText.textContent = `${job.completed} of ${job.total} tests completed`;
        renderSummary(job.summary);
        renderLogs(job.logs);
        if (job.status === "completed" || job.status === "failed") {
          progressText.textContent = job.status === "completed"
            ? `Completed ${job.completed} inference tests`
            : `Job failed: ${job.error || "Unknown error"}`;
          finishButton.hidden = false;
          finishButton.focus();
          break;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 500));
      }
    } catch (error) {
      progressText.textContent = error.message;
      finishButton.hidden = false;
    } finally {
      trigger.disabled = false;
    }
  };

  if (inferenceOverlay) {
    inferenceOverlay.querySelector("[data-inference-finish]").addEventListener("click", () => {
      window.location.reload();
    });
  }

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

  const providerSelections = [...document.querySelectorAll("[data-provider-select]")];
  const selectAllProviders = document.querySelector("[data-select-all]");
  const selectedCount = document.querySelector("[data-selected-count]");
  const bulkSubmit = document.querySelector("[data-bulk-submit]");
  const updateProviderSelection = () => {
    const count = providerSelections.filter((input) => input.checked).length;
    if (selectedCount) selectedCount.textContent = `${count} selected`;
    if (bulkSubmit) bulkSubmit.disabled = count === 0;
    if (selectAllProviders) {
      selectAllProviders.checked = count > 0 && count === providerSelections.length;
      selectAllProviders.indeterminate = count > 0 && count < providerSelections.length;
    }
  };
  providerSelections.forEach((input) => input.addEventListener("change", updateProviderSelection));
  if (selectAllProviders) {
    selectAllProviders.addEventListener("change", () => {
      providerSelections.forEach((input) => {
        input.checked = selectAllProviders.checked;
      });
      updateProviderSelection();
    });
  }

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

    const providerActions = event.target.closest("[data-provider-actions]");
    if (providerActions) {
      openModal({
        title: providerActions.dataset.providerName,
        subtitle: "Provider actions",
        body: providerActionsBody({
          id: providerActions.dataset.providerId,
          name: providerActions.dataset.providerName,
          modelCount: Number(providerActions.dataset.providerModelCount || 0),
        }),
        trigger: providerActions,
      });
      return;
    }

    const toggleModelSecret = event.target.closest("[data-toggle-model-secret]");
    if (toggleModelSecret) {
      const value = toggleModelSecret.parentElement.querySelector("[data-secret]");
      const isMasked = value.dataset.maskedSecret === "true";
      value.textContent = isMasked ? (value.dataset.secret || "Public endpoint / empty") : maskedSecret(value.dataset.secret);
      value.dataset.maskedSecret = isMasked ? "false" : "true";
      toggleModelSecret.title = isMasked ? "Hide API key" : "Show API key";
      toggleModelSecret.innerHTML = `<i data-lucide="${isMasked ? "eye-off" : "eye"}"></i>`;
      refreshIcons();
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

    const modelDetailButton = event.target.closest("[data-model-detail-url]");
    if (modelDetailButton) {
      modelDetailButton.disabled = true;
      try {
        const response = await fetch(modelDetailButton.dataset.modelDetailUrl, {
          headers: { Accept: "application/json" },
          cache: "no-store",
        });
        if (!response.ok) throw new Error("Model details could not be loaded.");
        const model = await response.json();
        openModal({
          title: model.model_id,
          subtitle: `${model.providers.length} available provider${model.providers.length === 1 ? "" : "s"}`,
          body: modelDetailBody(model),
          trigger: modelDetailButton,
          wide: true,
        });
      } catch (error) {
        openModal({
          title: "Unable to load model",
          body: messageBody(error.message),
          trigger: modelDetailButton,
        });
      } finally {
        modelDetailButton.disabled = false;
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
      if (form.hasAttribute("data-model-test-form")) {
        startModelTest(form, submitter);
      } else if (form.hasAttribute("data-app-restart-form")) {
        restartApplication(form, submitter);
      } else {
        form.dataset.confirmBypass = "true";
        form.requestSubmit(submitter);
      }
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
