const FILTERS = [
  { id: "all", label: "Все" },
  { id: "unrated", label: "Без отметки" },
  { id: "rated", label: "С отметкой" },
  { id: "ok", label: "ok" },
  { id: "fix", label: "fix_*" },
  { id: "spam", label: "Спам" },
  { id: "not_spam", label: "Не спам" },
];

const QUICK_RATES = [
  { value: "ok", label: "ok — всё верно", className: "btnOk" },
  { value: "fix_spam", label: "fix_spam", className: "btnFix" },
  { value: "fix_dept", label: "fix_dept", className: "btnFix" },
  { value: "fix_partner", label: "fix_partner", className: "btnFix" },
  { value: "fix_org", label: "fix_org", className: "btnFix" },
  { value: "fix_summary", label: "fix_summary", className: "btnFix" },
  { value: "", label: "Очистить", className: "btnDanger" },
];

let filter = "unrated";
let search = "";
let infoOnly = false;
let selectedIndex = null;
let listItems = [];
let searchTimer = null;
let attachmentPreviewUrl = null;
let attachmentPreviewLoadId = 0;

function $(id) {
  return document.getElementById(id);
}

function toast(msg, isError = false) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.toggle("error", isError);
  el.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 1800);
}

function ratePill(rate) {
  const r = (rate || "").trim();
  if (!r) return `<span class="pill pillEmpty">нет отметки</span>`;
  if (r.toLowerCase() === "ok") return `<span class="pill pillOk">${escapeHtml(r)}</span>`;
  return `<span class="pill pillFix">${escapeHtml(r)}</span>`;
}

function spamPill(decision) {
  if (decision === "spam") return `<span class="pill pillSpam">spam</span>`;
  if (decision === "uncertain") return `<span class="pill pillUncertain">uncertain</span>`;
  if (decision === "not_spam") return `<span class="pill pillOk">not_spam</span>`;
  return `<span class="pill pillEmpty">${escapeHtml(decision || "—")}</span>`;
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function sourcePriority(source) {
  const value = String(source || "").toLowerCase();
  if (value.startsWith("rag")) return 0;
  if (value === "rule_router" || value === "rule") return 1;
  if (value === "current_card") return 2;
  if (value === "allowlist") return 3;
  return 4;
}

function sortedCandidates(items) {
  return [...items].sort((a, b) => {
    const aScore = Number(a.score);
    const bScore = Number(b.score);
    const aHasScore = a.score != null && Number.isFinite(aScore);
    const bHasScore = b.score != null && Number.isFinite(bScore);
    const aSortScore = aHasScore ? aScore : -1;
    const bSortScore = bHasScore ? bScore : -1;
    if (aSortScore !== bSortScore) return bSortScore - aSortScore;
    const sourceDiff = sourcePriority(a.source) - sourcePriority(b.source);
    if (sourceDiff) return sourceDiff;
    return String(a.code || a.id || "").localeCompare(String(b.code || b.id || ""));
  });
}

function savedChoice(choice, field) {
  const value = choice[field];
  return Object.prototype.hasOwnProperty.call(choice, field) && value !== null && value !== "" ? value : undefined;
}

function preferredChoice(choice, field, options, valueKey) {
  const saved = savedChoice(choice, field);
  return saved !== undefined ? saved : sortedCandidates(options)[0]?.[valueKey];
}

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

async function refreshMeta() {
  const meta = await api("/api/meta");
  $("statsRow").innerHTML = [
    ["Всего", meta.total],
    ["Только info", meta.info ?? "—"],
    ["Без отметки", meta.unrated],
    ["С отметкой", meta.rated],
    ["Спам (decision)", meta.spam],
  ]
    .map(
      ([label, value]) =>
        `<div class="statCard"><div class="statLabel">${label}</div><div class="statValue">${value}</div></div>`
    )
    .join("");
}

function renderTabs() {
  $("filterTabs").innerHTML = FILTERS.map(
    (f) =>
      `<button class="tab ${filter === f.id ? "active" : ""}" data-filter="${f.id}">${f.label}</button>`
  ).join("");
  $("filterTabs").onclick = (e) => {
    const btn = e.target.closest("[data-filter]");
    if (!btn) return;
    filter = btn.dataset.filter;
    renderTabs();
    loadList();
  };
}

async function loadList() {
  const qs = new URLSearchParams({ filter, q: search });
  if (infoOnly) qs.set("info_only", "1");
  const data = await api(`/api/cards?${qs}`);
  listItems = data.items || [];
  $("listCount").textContent = `(${listItems.length})`;
  const list = $("cardList");
  if (!listItems.length) {
    list.innerHTML = `<div class="emptyState">Нет писем по фильтру</div>`;
    return;
  }
  list.innerHTML = listItems
    .map((item) => {
      const active = item.index === selectedIndex ? "active" : "";
      return `<button class="listItem ${active}" data-index="${item.index}">
        <div class="listSubject">${escapeHtml(item.subject || "(без темы)")}</div>
        <div class="listMeta">${escapeHtml(item.sender || "")} → ${escapeHtml(item.recipient || "")}</div>
        <div class="listMeta">${spamPill(item.spam_decision)} ${ratePill(item.rate_analitik)} · ${escapeHtml(item.department || item.department_code || "—")}</div>
      </button>`;
    })
    .join("");
  list.onclick = (e) => {
    const btn = e.target.closest("[data-index]");
    if (!btn) return;
    selectCard(Number(btn.dataset.index));
  };
}

async function selectCard(index) {
  selectedIndex = index;
  await loadList();
  const [data, candidateData, attachmentData] = await Promise.all([
    api(`/api/cards/${index}`),
    api(`/api/cards/${index}/candidates`),
    api(`/api/cards/${index}/attachments`).catch(() => ({ items: [], error: "load_failed" })),
  ]);
  const card = data.card;
  const spam = card.spam || {};
  const choice = card.operator_choice || {};
  const candidates = candidateData.routing || {};
  const deptOptions = sortedCandidates(candidates.department || []);
  const spamOptions = sortedCandidates(candidateData.spam || []);
  const organizationOptions = sortedCandidates(candidates.organization || []);
  const directionOptions = sortedCandidates(candidates.direction || []);
  const processOptions = sortedCandidates(candidates.process || []);
  const selectedSpam = preferredChoice(choice, "spam", spamOptions, "value");
  const selectedDepartment = preferredChoice(choice, "department_code", deptOptions, "code");
  const selectedOrganization = preferredChoice(choice, "organization", organizationOptions, "id");
  const selectedDirection = preferredChoice(choice, "direction", directionOptions, "id");
  const selectedProcess = preferredChoice(choice, "process", processOptions, "id");
  const warnings = candidateData.warnings || [];
  const panel = $("detailPanel");
  panel.innerHTML = `
    <div class="detailBody">
      <div class="navRow">
        <div>
          <div class="sectionTitle">Карточка #${index + 1}</div>
          <div style="font-weight:800;font-size:16px;margin-top:4px">${escapeHtml(card.subject || "(без темы)")}</div>
        </div>
        <div class="rateButtons">
          <button class="btn" id="prevBtn">← Prev</button>
          <button class="btn" id="nextBtn">Next →</button>
        </div>
      </div>

      <div class="section">
        <div class="sectionTitle">Метаданные</div>
        <dl class="kv">
          <dt>Отправитель</dt><dd>${escapeHtml(card.sender)}</dd>
          <dt>Получатель</dt><dd>${escapeHtml(card.recipient)}</dd>
          <dt>Дата</dt><dd>${escapeHtml(card.received_at || "")}</dd>
          <dt>Message-ID</dt><dd>${escapeHtml(card.message_id || "")}</dd>
        </dl>
      </div>

      <div class="section">
        <div class="sectionTitle">Текущий результат: спам</div>
        <div>${spamPill(spam.decision)} <span class="muted">${escapeHtml(spam.layer || "")}</span></div>
        <div class="box">${escapeHtml(spam.reason || "—")}</div>
      </div>

      <div class="section">
        <div class="sectionTitle">Краткий обзор</div>
        <div class="box">${escapeHtml(card.summary_ru || "—")}</div>
      </div>

      <div class="section">
        <div class="sectionTitle">Тело (excerpt)</div>
        <div class="box">${escapeHtml(card.body_excerpt || "—")}</div>
      </div>

      <div class="section">
        <div class="sectionTitle">Вложенные файлы</div>
        ${renderAttachments(index, attachmentData)}
      </div>

      <div class="selectionPanel">
        <div class="sectionTitle">Проверка RAG и выбор оператора</div>
        ${warnings.length ? `<div class="ragWarning">${escapeHtml(warnings.join(" · "))}. Показаны запасные варианты.</div>` : ""}
        <label class="fieldLabel">Спам: кандидаты RAG / правил
          <select id="spamSelect" class="choiceSelect">
            <option value="">Не выбрано</option>
            ${renderSpamOptions(spamOptions, selectedSpam)}
          </select>
        </label>
        <div class="candidateList">${renderSpamEvidence(spamOptions)}</div>
        ${renderChoiceField("organization", "Организация", organizationOptions, selectedOrganization, (choice.custom || {}).organization)}
        ${renderChoiceField("direction", "Направление", directionOptions, selectedDirection, (choice.custom || {}).direction)}
        <label class="fieldLabel">Отдел: кандидаты RAG / rule-router
          <select id="departmentSelect" class="choiceSelect">
            <option value="">Не выбрано</option>
            ${deptOptions.map((item) => `<option value="${escapeHtml(item.code)}" ${String(selectedDepartment || "") === String(item.code) ? "selected" : ""}>${escapeHtml(item.code)} — ${escapeHtml(item.name)}${item.score != null ? ` · score ${escapeHtml(item.score)}` : ""}</option>`).join("")}
          </select>
        </label>
        <div class="candidateList">${renderDepartmentEvidence(deptOptions)}</div>
        <label class="fieldLabel">Свой отдел: код
          <input id="customDepartmentCode" class="choiceInput" value="${escapeHtml((choice.custom || {}).department_code || "")}" placeholder="00-000000" />
        </label>
        <label class="fieldLabel">Свой отдел: название
          <input id="customDepartmentName" class="choiceInput" value="${escapeHtml((choice.custom || {}).department_name || "")}" placeholder="Название отдела" />
        </label>
        ${renderChoiceField("process", "Процесс", processOptions, selectedProcess, (choice.custom || {}).process)}
      </div>

      <div class="section">
        <div class="sectionTitle">XML</div>
        <div class="box xmlBox">${escapeHtml(card.xml_document || "(пусто — spam)")}</div>
      </div>

      <div class="ratePanel">
        <div class="sectionTitle">rate_analitik</div>
        <div class="rateButtons" id="quickRates"></div>
        <textarea id="rateInput" class="rateInput" placeholder="ok / fix_spam / fix_dept / свой комментарий">${escapeHtml(card.rate_analitik || "")}</textarea>
        <div class="navRow">
          <span class="muted">Выбор и отметка сохраняются в JSONL</span>
          <button class="btn btnPrimary" id="saveBtn">Сохранить выбор</button>
        </div>
      </div>
    </div>
  `;

  const quick = $("quickRates");
  quick.innerHTML = QUICK_RATES.map(
    (r) => `<button class="btn ${r.className}" data-rate="${escapeHtml(r.value)}">${r.label}</button>`
  ).join("");
  quick.onclick = async (e) => {
    const btn = e.target.closest("[data-rate]");
    if (!btn) return;
    $("rateInput").value = btn.dataset.rate;
    await saveCard(index, true);
  };

  $("saveBtn").onclick = async () => {
    await saveCard(index, true);
  };

  $("rateInput").addEventListener("keydown", async (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      await saveCard(index, true);
    }
  });

  $("prevBtn").onclick = () => jumpRelative(-1);
  $("nextBtn").onclick = () => jumpRelative(1);
  bindAttachmentActions(index);
}

function formatFileSize(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return "";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function attachmentErrorText(error) {
  if (error === "no_message_id") return "Message-ID отсутствует — вложения недоступны.";
  if (error === "not_in_mailbox") return "Письмо не найдено в IMAP — вложения недоступны.";
  if (error === "load_failed") return "Не удалось загрузить список вложений.";
  return "";
}

function attachmentPreviewKind(item) {
  const mime = String(item.mime_type || "").toLowerCase();
  const name = String(item.filename || "").toLowerCase();
  if (mime.startsWith("image/") || /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(name)) {
    return "image";
  }
  if (mime === "application/pdf" || name.endsWith(".pdf")) {
    return "pdf";
  }
  return null;
}

function closeAttachmentModal() {
  attachmentPreviewLoadId += 1;
  const modal = $("attachmentModal");
  if (!modal) return;
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  if (attachmentPreviewUrl) {
    URL.revokeObjectURL(attachmentPreviewUrl);
    attachmentPreviewUrl = null;
  }
  const body = $("attachmentModalBody");
  if (body) body.innerHTML = `<div class="attachmentModalLoading">Загружаем из IMAP…</div>`;
}

async function openAttachmentModal(cardIndex, attIndex, filename, kind) {
  const modal = $("attachmentModal");
  const body = $("attachmentModalBody");
  const title = $("attachmentModalTitle");
  if (!modal || !body || !title) return;

  closeAttachmentModal();
  const loadId = attachmentPreviewLoadId;
  title.textContent = filename || `Вложение ${attIndex + 1}`;
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  body.innerHTML = `<div class="attachmentModalLoading">Загружаем из IMAP…</div>`;

  try {
    const res = await fetch(`/api/cards/${cardIndex}/attachments/${attIndex}`);
    if (loadId !== attachmentPreviewLoadId) return;
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || res.statusText || "Ошибка загрузки");
    }
    const blob = await res.blob();
    if (loadId !== attachmentPreviewLoadId) return;
    attachmentPreviewUrl = URL.createObjectURL(blob);
    if (kind === "image") {
      body.innerHTML = "";
      const img = document.createElement("img");
      img.className = "attachmentModalImage";
      img.src = attachmentPreviewUrl;
      img.alt = filename || "Вложение";
      body.appendChild(img);
      return;
    }
    if (kind === "pdf") {
      body.innerHTML = "";
      const frame = document.createElement("iframe");
      frame.className = "attachmentModalPdf";
      frame.src = attachmentPreviewUrl;
      frame.title = filename || "PDF";
      body.appendChild(frame);
      return;
    }
    throw new Error("Формат не поддерживается для просмотра");
  } catch (err) {
    if (loadId !== attachmentPreviewLoadId) return;
    body.innerHTML = `<div class="attachmentModalError">${escapeHtml(String(err.message || err))}</div>`;
  }
}

function renderAttachments(cardIndex, attachmentData) {
  const items = attachmentData.items || [];
  const error = attachmentErrorText(attachmentData.error);
  if (error) {
    return `<div class="muted">${escapeHtml(error)}</div>`;
  }
  if (!items.length) {
    return `<div class="muted">Нет вложений</div>`;
  }
  return `<div class="fileList">${items
    .map((item) => {
      const sizeLabel = formatFileSize(item.size_bytes);
      const meta = [sizeLabel, item.mime_type].filter(Boolean).join(" · ");
      const previewKind = attachmentPreviewKind(item);
      const viewButton = previewKind
        ? `<button type="button" class="btn btnPrimary" data-view-index="${item.index}" data-view-kind="${previewKind}" data-view-filename="${escapeHtml(item.filename || "")}">Просмотр</button>`
        : "";
      return `<div class="fileRow">
        <div>
          <div class="fileName">${escapeHtml(item.filename || `Файл ${item.index + 1}`)}</div>
          ${meta ? `<div class="fileMeta">${escapeHtml(meta)}</div>` : ""}
          <div class="attachmentPreview hidden" id="attachmentPreview-${item.index}"></div>
        </div>
        <div class="fileActions">
          ${viewButton}
          <a class="btn btnSecondary" href="/api/cards/${cardIndex}/attachments/${item.index}" download>Скачать</a>
          <button type="button" class="btn" data-preview-index="${item.index}">Текст</button>
        </div>
      </div>`;
    })
    .join("")}</div>`;
}

function bindAttachmentActions(cardIndex) {
  document.querySelectorAll("[data-view-index]").forEach((button) => {
    button.onclick = () => {
      openAttachmentModal(
        cardIndex,
        Number(button.dataset.viewIndex),
        button.dataset.viewFilename || "",
        button.dataset.viewKind || "image"
      );
    };
  });
  document.querySelectorAll("[data-preview-index]").forEach((button) => {
    button.onclick = async () => {
      const attIndex = Number(button.dataset.previewIndex);
      const preview = $(`attachmentPreview-${attIndex}`);
      if (!preview) return;
      if (!preview.classList.contains("hidden") && preview.textContent.trim()) {
        preview.classList.add("hidden");
        preview.textContent = "";
        return;
      }
      preview.classList.remove("hidden");
      preview.textContent = "Загружаем текст вложения…";
      try {
        const data = await api(`/api/cards/${cardIndex}/attachments/${attIndex}/text`);
        if (data.error === "attachment_unavailable") {
          preview.textContent = "Текст недоступен (файл слишком большой или формат не поддерживается).";
          return;
        }
        preview.textContent = data.text || "Текст не извлечён (формат не поддерживается).";
      } catch (err) {
        preview.textContent = String(err.message || err);
      }
    };
  });
}

function renderChoiceField(id, label, options, selected, custom = "") {
  return `<label class="fieldLabel">${label}
    <select id="${id}Select" class="choiceSelect">
      <option value="">Не выбрано</option>
      ${options.map((item) => `<option value="${escapeHtml(item.id)}" ${String(selected || "") === String(item.id) ? "selected" : ""}>${escapeHtml(item.id)}${item.name && item.name !== item.id ? ` — ${escapeHtml(item.name)}` : ""}</option>`).join("")}
    </select>
  </label>
  <label class="fieldLabel">Свой вариант для «${label}»
    <input id="custom${id[0].toUpperCase() + id.slice(1)}" class="choiceInput" value="${escapeHtml(custom)}" placeholder="Введите, если нужного варианта нет" />
  </label>`;
}

function renderSpamOptions(items, selected) {
  const values = new Map([[true, "spam"], [false, "not_spam"]]);
  for (const item of items) values.set(Boolean(item.value), item.label || (item.value ? "spam" : "not_spam"));
  return [...values].map(([value, label]) => `<option value="${value}" ${selected === value ? "selected" : ""}>${escapeHtml(label)}</option>`).join("");
}

function renderSpamEvidence(items) {
  return items.map((item) => `<div class="candidateItem"><b>${escapeHtml(item.label || "—")}</b> · ${escapeHtml(item.source || "")}${item.score != null ? ` · score ${escapeHtml(item.score)}` : ""}<br><span>${escapeHtml(item.reason || item.snippet || "—")}</span></div>`).join("") || `<div class="muted">Совпадений нет.</div>`;
}

function renderDepartmentEvidence(items) {
  return items.map((item) => `<div class="candidateItem"><b>${escapeHtml(item.code)} — ${escapeHtml(item.name)}</b>${item.score != null ? ` · score ${escapeHtml(item.score)}` : ""} · ${escapeHtml(item.source || "")}<br><span>${escapeHtml(item.reason || item.snippet || (item.keywords || []).join(", ") || "—")}</span></div>`).join("") || `<div class="muted">RAG не вернул совпадений; используйте allowlist или свой вариант.</div>`;
}

function selectedOrCustom(selectId, customId) {
  return $(customId).value.trim() || $(selectId).value;
}

async function saveCard(index, goNext) {
  try {
    const departmentCode = selectedOrCustom("departmentSelect", "customDepartmentCode");
    const department = [...$("departmentSelect").options].find((option) => option.value === departmentCode);
    const departmentName = $("customDepartmentName").value.trim() || (department ? department.text.replace(/^[^—]+—\s*/, "").replace(/\s· score.*$/, "") : "");
    const operatorChoice = {
      organization: selectedOrCustom("organizationSelect", "customOrganization"),
      direction: selectedOrCustom("directionSelect", "customDirection"),
      department_code: departmentCode,
      department_name: departmentName,
      process: selectedOrCustom("processSelect", "customProcess"),
      spam: $("spamSelect").value === "" ? null : $("spamSelect").value === "true",
      custom: {
        organization: $("customOrganization").value.trim() || undefined,
        direction: $("customDirection").value.trim() || undefined,
        department_code: $("customDepartmentCode").value.trim() || undefined,
        department_name: $("customDepartmentName").value.trim() || undefined,
        process: $("customProcess").value.trim() || undefined,
      },
      rag_candidates: (await api(`/api/cards/${index}/candidates`)),
    };
    await api(`/api/cards/${index}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rate_analitik: $("rateInput").value, operator_choice: operatorChoice }),
    });
    toast("Выбор оператора сохранён");
    await refreshMeta();
    await loadList();
    if (goNext) jumpRelative(1);
    else await selectCard(index);
  } catch (err) {
    toast(String(err.message || err), true);
  }
}

function jumpRelative(delta) {
  if (!listItems.length) return;
  const pos = listItems.findIndex((x) => x.index === selectedIndex);
  const nextPos = pos < 0 ? 0 : Math.min(listItems.length - 1, Math.max(0, pos + delta));
  selectCard(listItems[nextPos].index);
}

function bindSearch() {
  $("searchInput").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      search = e.target.value.trim();
      loadList();
    }, 250);
  });
  $("infoOnlyBtn").addEventListener("click", () => {
    infoOnly = !infoOnly;
    $("infoOnlyBtn").classList.toggle("active", infoOnly);
    $("infoOnlyBtn").setAttribute("aria-pressed", infoOnly ? "true" : "false");
    loadList();
  });
}

function bindAttachmentModal() {
  $("attachmentModalClose")?.addEventListener("click", closeAttachmentModal);
  $("attachmentModalBackdrop")?.addEventListener("click", closeAttachmentModal);
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("attachmentModal")?.classList.contains("hidden")) {
    e.preventDefault();
    closeAttachmentModal();
    return;
  }
  if (e.target.matches("textarea, input")) return;
  if (e.key === "j" || e.key === "ArrowDown") {
    e.preventDefault();
    jumpRelative(1);
  }
  if (e.key === "k" || e.key === "ArrowUp") {
    e.preventDefault();
    jumpRelative(-1);
  }
  if (e.key === "o") {
    e.preventDefault();
    if (selectedIndex != null) {
      $("rateInput").value = "ok";
      saveCard(selectedIndex, true);
    }
  }
});

async function init() {
  renderTabs();
  bindSearch();
  bindAttachmentModal();
  await refreshMeta();
  await loadList();
  if (listItems.length) selectCard(listItems[0].index);
}

init().catch((err) => toast(String(err.message || err), true));
