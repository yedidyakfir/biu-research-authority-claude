(function () {
  "use strict";

  const report = JSON.parse(document.getElementById("report-data").textContent);
  const selectedSheet = report.sheets.find(sheet => sheet.name === report.source.sheet);
  const header = selectedSheet.rows[report.source.header_row - 1] || [];
  const categories = new Map(report.foundation.categories.map(category => [category.id, category]));
  const records = report.records.filter(record => record.kind === "transaction");
  const draftKey = `fund-report-review:${report.report_id}`;
  const statusText = { classified: "סווג", needs_review: "דורש בדיקה", not_expense: "לא הוצאה" };
  const display = report.source.display_columns || {};
  let filter = "pending";
  let search = "";
  let limit = 12;

  const $ = id => document.getElementById(id);
  const cellValue = cell => cell == null ? "" : typeof cell === "object" ? (cell.display ?? cell.v ?? "") : cell;
  const text = value => String(value ?? "");
  const rowCells = record => selectedSheet.rows[record.row - 1] || [];
  const at = (record, index) => index == null ? "" : cellValue(rowCells(record)[index]);
  const pending = record => {
    const c = record.classification;
    if (c.status === "needs_review") return true;
    if (c.status === "classified") return !c.category_id || !text(c.rationale).trim();
    if (c.status === "not_expense") return !!c.category_id || !text(c.rationale).trim();
    return true;
  };
  const warningFor = record => {
    const c = record.classification;
    if (c.status === "classified" && !c.category_id) return "צריך לבחור סיווג לפני השלמת הבדיקה.";
    if (c.status === "classified" && !text(c.rationale).trim()) return "צריך להסביר בקצרה למה נבחר הסיווג.";
    if (c.status === "not_expense" && !text(c.rationale).trim()) return "צריך להסביר למה התנועה אינה הוצאה.";
    if (c.status === "needs_review" && !text(c.uncertainty_reason).trim()) return "צריך לכתוב מה חסר כדי להחליט.";
    return text(c.uncertainty_reason);
  };

  function loadDraft() {
    try {
      const saved = JSON.parse(localStorage.getItem(draftKey) || "null");
      if (!saved || saved.source_sha256 !== report.source.sha256 || !Array.isArray(saved.edits)) return;
      const byRow = new Map(saved.edits.map(item => [item.row, item.classification]));
      for (const record of records) {
        const edit = byRow.get(record.row);
        if (edit) record.classification = { ...record.classification, ...edit };
      }
    } catch (error) {
      $("saveState").textContent = "שמירה מקומית אינה זמינה";
    }
  }

  function saveDraft() {
    try {
      const edits = records.map(record => ({ row: record.row, classification: record.classification }));
      localStorage.setItem(draftKey, JSON.stringify({ source_sha256: report.source.sha256, edits }));
      $("saveState").textContent = "השינויים נשמרים בדפדפן";
    } catch (error) {
      $("saveState").textContent = "שמירה מקומית אינה זמינה";
    }
  }

  function countByStatus() {
    return {
      total: records.length,
      pending: records.filter(pending).length,
      classified: records.filter(record => record.classification.status === "classified" && !pending(record)).length,
      notExpense: records.filter(record => record.classification.status === "not_expense" && !pending(record)).length,
    };
  }

  function updateOverview() {
    const counts = countByStatus();
    $("pendingHero").textContent = counts.pending.toLocaleString("he-IL");
    $("pendingSection").textContent = counts.pending.toLocaleString("he-IL");
    $("progressText").textContent = `${(counts.total - counts.pending).toLocaleString("he-IL")} מתוך ${counts.total.toLocaleString("he-IL")} טופלו`;
    $("completionBar").style.width = `${counts.total ? (counts.total - counts.pending) / counts.total * 100 : 100}%`;
  }

  function amountFor(record) {
    const raw = at(record, display.amount);
    if (raw === "") return "";
    const normalized = typeof raw === "number" ? raw : Number(String(raw).replace(/,/g, ""));
    return Number.isFinite(normalized) ? normalized.toLocaleString("he-IL", { maximumFractionDigits: 2 }) : text(raw);
  }

  function searchText(record) {
    return [record.row, ...rowCells(record).map(cellValue), record.classification.rationale, record.classification.uncertainty_reason].join(" ").toLocaleLowerCase();
  }

  function filteredRecords() {
    return records.filter(record => {
      if (filter === "pending" && !pending(record)) return false;
      if (filter === "classified" && (record.classification.status !== "classified" || pending(record))) return false;
      if (filter === "not_expense" && (record.classification.status !== "not_expense" || pending(record))) return false;
      return !search || searchText(record).includes(search);
    });
  }

  function detailList(record) {
    const detail = document.createElement("details");
    detail.className = "original-details";
    const summary = document.createElement("summary");
    summary.textContent = "כל שדות המקור";
    const list = document.createElement("dl");
    rowCells(record).forEach((cell, index) => {
      if (cell == null || cellValue(cell) === "") return;
      const group = document.createElement("div");
      const term = document.createElement("dt");
      term.textContent = text(cellValue(header[index]) || `עמודה ${index + 1}`);
      const value = document.createElement("dd");
      value.textContent = text(cellValue(cell));
      group.append(term, value);
      list.append(group);
    });
    detail.append(summary, list);
    return detail;
  }

  function makeCard(record) {
    const card = document.createElement("article");
    card.innerHTML = '<div class="record-top"><div><div class="record-meta"><span class="row-id"></span><span class="record-badge"></span><span class="record-account"></span><span class="record-category"></span></div><h3 class="record-title"></h3></div><div class="record-amount"></div></div><p class="record-reason"></p><div class="record-actions-top"><button type="button" class="edit-toggle">עריכה</button></div><div class="edit-panel"><div class="edit-grid"><label class="field">סיווג<select class="category-select"></select></label><label class="field">נימוק קצר<input class="rationale" type="text" placeholder="למה נבחר הסיווג"></label></div><div class="decision-actions"><button type="button" class="button button-primary confirm-classification">אישור סיווג</button><button type="button" class="button button-outline mark-non-expense">לא הוצאה</button><button type="button" class="button button-danger-quiet mark-pending">השאר לבדיקה</button><span class="action-feedback" role="alert"></span></div><details class="uncertainty-edit"><summary>עריכת סיבת הבדיקה</summary><label class="field">מה חסר כדי להחליט?<textarea class="uncertainty" rows="2"></textarea></label></details></div>';
    card.querySelector(".row-id").textContent = `שורה ${record.row}`;
    card.querySelector(".record-account").textContent = text(at(record, display.account));
    const description = text(at(record, display.description)).trim();
    card.querySelector(".record-title").textContent = description ? description.slice(0, 200) : text(at(record, display.reference)).slice(0, 200) || `תנועה בשורה ${record.row}`;
    card.querySelector(".record-amount").textContent = amountFor(record);
    const warning = card.querySelector(".record-reason");
    const editToggle = card.querySelector(".edit-toggle");
    const editPanel = card.querySelector(".edit-panel");
    const feedback = card.querySelector(".action-feedback");
    const refreshCard = () => {
      const needsReview = pending(record);
      card.className = `record-card ${needsReview ? "pending" : record.classification.status === "not_expense" ? "not-expense" : ""}`;
      card.querySelector(".record-badge").textContent = needsReview ? "דורש בדיקה" : statusText[record.classification.status];
      const category = categories.get(record.classification.category_id);
      card.querySelector(".record-category").textContent = !needsReview && category ? category.label_he : "";
      warning.textContent = warningFor(record);
      warning.hidden = !needsReview;
      editToggle.hidden = needsReview;
      if (needsReview) editPanel.hidden = false;
      card.querySelector(".mark-pending").hidden = record.classification.status === "needs_review";
    };
    const categorySelect = card.querySelector(".category-select");
    categorySelect.add(new Option("בחרו סיווג", ""));
    for (const category of report.foundation.categories) categorySelect.add(new Option(category.label_he, category.id));
    categorySelect.value = record.classification.category_id || "";
    categorySelect.addEventListener("change", () => {
      record.classification.category_id = categorySelect.value || null;
      feedback.textContent = "";
      saveDraft(); updateOverview(); refreshCard();
    });
    const rationale = card.querySelector(".rationale");
    rationale.value = text(record.classification.rationale);
    rationale.addEventListener("input", () => { record.classification.rationale = rationale.value; feedback.textContent = ""; saveDraft(); updateOverview(); refreshCard(); });
    const uncertainty = card.querySelector(".uncertainty");
    uncertainty.value = text(record.classification.uncertainty_reason);
    uncertainty.addEventListener("input", () => {
      record.classification.uncertainty_reason = uncertainty.value;
      feedback.textContent = "";
      saveDraft(); refreshCard();
    });
    editToggle.addEventListener("click", () => {
      editPanel.hidden = !editPanel.hidden;
      editToggle.textContent = editPanel.hidden ? "עריכה" : "סגירה";
    });
    const complete = status => {
      if (status === "classified" && !record.classification.category_id) {
        feedback.textContent = "בחרו סיווג.";
        categorySelect.focus();
        return;
      }
      if (!rationale.value.trim()) {
        feedback.textContent = status === "not_expense" ? "כתבו למה זו אינה הוצאה." : "כתבו נימוק קצר.";
        rationale.focus();
        return;
      }
      record.classification.status = status;
      record.classification.rationale = rationale.value.trim();
      record.classification.uncertainty_reason = "";
      if (status === "not_expense") record.classification.category_id = null;
      saveDraft(); updateOverview(); renderList();
    };
    card.querySelector(".confirm-classification").addEventListener("click", () => complete("classified"));
    card.querySelector(".mark-non-expense").addEventListener("click", () => complete("not_expense"));
    card.querySelector(".mark-pending").addEventListener("click", () => {
      if (!uncertainty.value.trim()) {
        feedback.textContent = "כתבו מה חסר כדי להחליט.";
        card.querySelector(".uncertainty-edit").open = true;
        uncertainty.focus();
        return;
      }
      record.classification.status = "needs_review";
      record.classification.uncertainty_reason = uncertainty.value.trim();
      saveDraft(); updateOverview(); renderList();
    });
    editPanel.hidden = !pending(record);
    refreshCard();
    card.append(detailList(record));
    return card;
  }

  function renderList() {
    const filtered = filteredRecords();
    const list = $("recordList");
    list.replaceChildren();
    filtered.slice(0, limit).forEach(record => list.append(makeCard(record)));
    if (!filtered.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      const strong = document.createElement("strong");
      strong.textContent = filter === "pending" && !search ? "אין תנועות שמחכות לבדיקה" : "לא נמצאו תנועות";
      const hint = document.createElement("span");
      hint.textContent = filter === "pending" && !search ? "אפשר לעבור ל׳כל התנועות׳ כדי לראות את הדוח המלא." : "אפשר לשנות את החיפוש או את המסנן.";
      empty.append(strong, hint); list.append(empty);
    }
    $("resultsInfo").textContent = `${Math.min(filtered.length, limit).toLocaleString("he-IL")} מתוך ${filtered.length.toLocaleString("he-IL")}`;
    $("showMore").hidden = filtered.length <= limit;
  }

  function download(filename, blob) {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }

  function saveJson() {
    download(`fund-report-review-${report.report_id}.json`, new Blob([JSON.stringify(report, null, 2)], { type: "application/json;charset=utf-8" }));
    $("exportStatus").textContent = "קובץ JSON נשמר עם ההחלטות הנוכחיות.";
  }

  function invalidRows() {
    return records.filter(record => {
      const c = record.classification;
      if (c.category_id && !categories.has(c.category_id)) return true;
      if (c.status === "classified") return !c.category_id || !text(c.rationale).trim();
      if (c.status === "needs_review") return !text(c.uncertainty_reason).trim();
      if (c.status === "not_expense") return !!c.category_id || !text(c.rationale).trim();
      return true;
    });
  }

  function exportExcel() {
    const invalid = invalidRows();
    if (invalid.length) {
      $("exportStatus").textContent = `${invalid.length} תנועות חסרות סיווג או הסבר נדרש. השלימו אותן לפני הייצוא.`;
      filter = "pending";
      document.querySelectorAll("[data-filter]").forEach(button => button.classList.toggle("selected", button.dataset.filter === filter));
      renderList();
      $("review").scrollIntoView({ behavior: "smooth" });
      return;
    }
    try {
      const bytes = window.FundXlsx.buildXlsx(report);
      download(`fund-report-reviewed-${report.report_id}.xlsx`, new Blob([bytes], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
      const count = countByStatus();
      $("exportStatus").textContent = count.pending ? `קובץ Excel נשמר עם ${count.pending} תנועות שעדיין מסומנות לבדיקה.` : "קובץ Excel נשמר.";
    } catch (error) {
      $("exportStatus").textContent = `הייצוא נכשל: ${error.message}`;
    }
  }

  async function importJson(file) {
    try {
      const incoming = JSON.parse(await file.text());
      if (incoming.source?.sha256 !== report.source.sha256 || incoming.foundation?.id !== report.foundation.id || !Array.isArray(incoming.records)) {
        throw new Error("קובץ JSON אינו שייך לדוח הזה");
      }
      const edits = new Map(incoming.records.map(record => [record.row, record.classification]));
      if (edits.size !== report.records.length) throw new Error("מספר השורות בקובץ אינו תואם לדוח");
      for (const record of records) {
        const next = edits.get(record.row);
        if (!next || !["classified", "needs_review", "not_expense"].includes(next.status)) throw new Error(`סיווג לא תקין בשורה ${record.row}`);
        if (next.category_id && !categories.has(next.category_id)) throw new Error(`קטגוריה לא מוכרת בשורה ${record.row}`);
        record.classification = { ...record.classification, ...next };
      }
      saveDraft(); updateOverview(); renderList();
      $("exportStatus").textContent = "החלטות הבדיקה נטענו מקובץ JSON.";
    } catch (error) {
      $("exportStatus").textContent = `לא ניתן לטעון את JSON: ${error.message}`;
    }
  }

  function updateReadingProgress() {
    const available = document.documentElement.scrollHeight - window.innerHeight;
    $("readingProgress").style.width = `${available > 0 ? Math.min(100, window.scrollY / available * 100) : 100}%`;
    const sections = ["overview", "review", "export"];
    let active = "overview";
    for (const id of sections) if ($(id).getBoundingClientRect().top < 140) active = id;
    document.querySelectorAll(".section-nav a").forEach(link => link.classList.toggle("active", link.hash === `#${active}`));
  }

  loadDraft();
  $("fileName").textContent = report.source.filename;
  $("foundationName").textContent = `${report.foundation.name_he} · ${report.foundation.program_he}`;
  $("policyScope").textContent = `${report.foundation.scope_note_he || report.foundation.scope_note} זכאות להוצאה לא נקבעה כאן.`;
  const sourceUrl = report.foundation.source?.url || "";
  $("sourceLink").href = /^https:\/\//i.test(sourceUrl) ? sourceUrl : "#overview";
  updateOverview(); renderList(); updateReadingProgress();
  document.querySelectorAll("[data-filter]").forEach(button => button.addEventListener("click", () => {
    filter = button.dataset.filter; limit = 12;
    document.querySelectorAll("[data-filter]").forEach(other => other.classList.toggle("selected", other === button));
    renderList();
  }));
  $("search").addEventListener("input", event => { search = event.target.value.trim().toLocaleLowerCase(); limit = 12; renderList(); });
  $("showMore").addEventListener("click", () => { limit += 12; renderList(); });
  $("saveJson").addEventListener("click", saveJson);
  $("exportXlsx").addEventListener("click", exportExcel);
  $("importJson").addEventListener("click", () => $("jsonPicker").click());
  $("jsonPicker").addEventListener("change", event => { if (event.target.files[0]) importJson(event.target.files[0]); event.target.value = ""; });
  window.addEventListener("scroll", updateReadingProgress, { passive: true });
})();
