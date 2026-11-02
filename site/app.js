"use strict";

(() => {
  const topicNames = {
    performance: "실적·자본·주주정책",
    digital: "디지털·AI·신사업",
    expansion: "제휴·인수·해외사업",
    strategy: "조직·경영전략",
    risk: "규제·위험·운영"
  };
  const $ = id => document.getElementById(id);
  const state = { latest: null, status: null, topic: "", selected: null, detail: null, request: 0, loadRequest: 0 };
  let toastTimer;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function formatDate(value, includeTime = false) {
    if (!value) return "날짜 미확인";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "날짜 미확인";
    const parts = new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", ...(includeTime ? { hour: "2-digit", minute: "2-digit", hourCycle: "h23" } : {}) }).formatToParts(date);
    const p = Object.fromEntries(parts.map(part => [part.type, part.value]));
    return `${p.year}.${p.month}.${p.day}${includeTime ? ` · ${p.hour}:${p.minute}` : ""}`;
  }

  async function getJSON(path) {
    const response = await fetch(path, { cache: "no-store" });
    if (!response.ok) throw new Error(`자료 요청 실패 (${response.status})`);
    try { return await response.json(); }
    catch { throw new Error("마트 파일을 읽을 수 없습니다. JSON 형식을 확인해주세요."); }
  }

  function showError(message) {
    const banner = $("error-banner");
    banner.replaceChildren(el("span", "", message));
    const retry = el("button", "", "다시 불러오기");
    retry.type = "button";
    retry.addEventListener("click", loadData);
    banner.append(retry);
    banner.hidden = false;
  }

  function emptyList(title, message) {
    const wrap = el("div", "empty-state");
    const symbol = el("span", "empty-symbol", "◇");
    symbol.setAttribute("aria-hidden", "true");
    wrap.append(symbol, el("strong", "", title), el("p", "", message));
    return wrap;
  }

  function metric(id, value, unit) {
    const target = $(id);
    target.replaceChildren(document.createTextNode(String(value)), el("small", "", unit));
  }

  function renderSummary() {
    const { latest, status } = state;
    metric("issue-count", latest.candidates.length, "건");
    metric("article-count", status.article_count ?? "—", "건");
    metric("version-count", status.version_count ?? "—", "개");
    $("last-run").textContent = formatDate(status.last_run_at || latest.generated_at, true);
    $("run-count").textContent = `${status.run_count ?? "—"}회`;
    const week = /^(\d{4})-W(\d{1,2})$/.exec(latest.report_week || "");
    $("week-label").textContent = week ? `${week[1]} / WEEK ${week[2]}` : "DEMO EDITION";
    $("week-number").textContent = week ? week[2] : "01";
    $("edition-year").textContent = week ? `${week[1]} · DEMO` : "가상 데이터 데모";
    $("all-count").textContent = latest.candidates.length;
    document.querySelectorAll("[data-count]").forEach(node => {
      node.textContent = latest.candidates.filter(item => item.topic_id === node.dataset.count).length;
    });
    const companySelect = $("company-filter");
    const previousCompany = companySelect.value;
    companySelect.replaceChildren(new Option("모든 회사", ""));
    [...new Set(latest.candidates.map(item => item.company))].filter(Boolean).sort((a,b) => a.localeCompare(b,"ko")).forEach(company => companySelect.add(new Option(company, company)));
    if ([...companySelect.options].some(option => option.value === previousCompany)) companySelect.value = previousCompany;
  }

  function visibleCandidates() {
    if (!state.latest) return [];
    const query = $("search").value.trim().toLocaleLowerCase("ko");
    const company = $("company-filter").value;
    const result = state.latest.candidates.filter(item => (!state.topic || item.topic_id === state.topic) && (!company || item.company === company) && (!query || [item.title, item.company, item.topic, item.why_now, ...(item.bullets || [])].join(" ").toLocaleLowerCase("ko").includes(query)));
    return result.sort((a,b) => $("sort-order").value === "title" ? a.title.localeCompare(b.title,"ko") : ((a.priority === "high" ? 0 : 1) - (b.priority === "high" ? 0 : 1) || a.title.localeCompare(b.title,"ko")));
  }

  function renderCandidates() {
    if (!state.latest) return;
    const items = visibleCandidates();
    const list = $("candidate-list");
    list.replaceChildren();
    $("visible-count").textContent = `${items.length}건`;
    $("active-topic-label").textContent = topicNames[state.topic] || "전체 이슈";
    document.querySelectorAll(".topic-button").forEach(button => {
      const active = button.dataset.topic === state.topic;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    if (!items.length) {
      list.append(emptyList("조건에 맞는 후보가 없습니다", "검색어나 주제·회사 필터를 바꿔보세요."));
      state.selected = null;
      state.detail = null;
      state.request++;
      $("detail-panel").setAttribute("aria-busy", "false");
      $("detail-panel").replaceChildren(emptyList("선택된 이슈가 없습니다", "필터를 초기화하면 전체 후보를 확인할 수 있습니다."));
      return;
    }
    items.forEach(item => {
      const button = el("button", `candidate${state.selected === item.issue_id ? " selected" : ""}`);
      button.type = "button";
      button.dataset.issue = item.issue_id;
      button.setAttribute("aria-pressed", String(state.selected === item.issue_id));
      button.setAttribute("aria-controls", "detail-panel");
      const top = el("div", "candidate-top");
      top.append(el("span", `priority ${item.priority === "high" ? "high" : ""}`, item.priority === "high" ? "우선 검토" : "일반 검토"), el("span", "company-name", item.company));
      const bottom = el("div", "candidate-bottom");
      bottom.append(el("span", "", item.topic || topicNames[item.topic_id]), el("span", "", "가상 시나리오"));
      const arrow = el("span", "candidate-arrow", "↗");
      arrow.setAttribute("aria-hidden", "true");
      button.append(top, el("h3", "", item.title), el("p", "candidate-reason", item.why_now), bottom, arrow);
      button.addEventListener("click", () => selectIssue(item));
      list.append(button);
    });
    if (!items.some(item => item.issue_id === state.selected)) selectIssue(items[0]);
  }

  function detailSection(number, title) {
    const section = el("section", "detail-section");
    const heading = el("h4");
    heading.append(el("span", "section-number", number), document.createTextNode(title));
    section.append(heading);
    return section;
  }

  function renderDetail(detail) {
    const panel = $("detail-panel");
    panel.replaceChildren();
    const kicker = el("div", "detail-kicker");
    kicker.append(el("span", "", "ISSUE BRIEF"), el("span", "", "가상 데이터"));
    const meta = el("div", "detail-meta");
    meta.append(el("span", "", detail.company), el("span", "", "등록된 보고 이력 없음"));
    panel.append(kicker, el("div", "detail-topic", detail.topic), el("h3", "detail-title", detail.title), meta);
    const why = detailSection("01", "왜 살펴봐야 하나요");
    why.append(el("p", "reason-box", detail.why_now));
    const draft = detailSection("02", "보고 초안");
    draft.querySelector("h4").append(el("span", "draft-label", "규칙 기반 데모"));
    const bullets = el("ul", "brief-bullets");
    detail.bullets.forEach(text => bullets.append(el("li", "", text)));
    draft.append(bullets, el("p", "report-note", "등록된 실제 보고 이력이 없어 기보고 대비 비교는 제공하지 않습니다. 이 초안은 실제 보고 완료를 의미하지 않습니다."));
    const evidenceSection = detailSection("03", "확인한 근거");
    if (detail.change_summary) evidenceSection.append(el("p", "change-note", detail.change_summary));
    detail.evidence.forEach(evidence => {
      const article = el("article", "evidence-item");
      const label = el("div", "evidence-label");
      label.append(el("span", "", evidence.source_name || "가상 시나리오"), el("span", "", formatDate(evidence.published_at)));
      article.append(label, el("p", "evidence-title", evidence.title));
      const disclosure = el("details");
      disclosure.append(el("summary", "", "가상 원자료 보기"), el("div", "evidence-text", evidence.content_text || "원자료 본문 없음"));
      article.append(disclosure);
      evidenceSection.append(article);
    });
    evidenceSection.append(el("p", "evidence-footnote", "근거는 저장소에 포함된 가상 시나리오입니다. 실제 언론 기사나 외부 출처를 인용하지 않았습니다."));
    const actions = el("div", "detail-actions");
    const copy = el("button", "copy-button", "보고 초안 복사");
    copy.type = "button";
    copy.addEventListener("click", () => copyDraft(detail));
    actions.append(copy, el("span", "", "복사한 내용은 저장되지 않으며, 실제 보고 이력에 반영되지 않습니다."));
    panel.append(why, draft, evidenceSection, actions);
  }

  async function selectIssue(item) {
    if (!state.latest || !state.latest.candidates.some(candidate => candidate === item)) return;
    const request = ++state.request;
    state.selected = item.issue_id;
    state.detail = null;
    document.querySelectorAll(".candidate").forEach(button => {
      const active = button.dataset.issue === state.selected;
      button.classList.toggle("selected", active);
      button.setAttribute("aria-pressed", String(active));
    });
    const panel = $("detail-panel");
    panel.setAttribute("aria-busy", "true");
    panel.replaceChildren(emptyList("이슈 상세를 불러오고 있습니다", "초안과 근거 자료를 확인합니다."));
    try {
      if (typeof item.detail_path !== "string" || !/^data\/issues\/[A-Za-z0-9_-]+\.json$/.test(item.detail_path)) throw new Error("이슈 상세 경로가 올바르지 않습니다.");
      const detail = await getJSON(item.detail_path);
      if (request !== state.request) return;
      if (detail.is_demo !== true || detail.issue_id !== item.issue_id || detail.generation_id !== state.latest.generation_id) throw new Error("목록과 상세의 생성 버전이 다릅니다. 자료를 다시 불러와 주세요.");
      if (!Array.isArray(detail.bullets) || !Array.isArray(detail.evidence)) throw new Error("상세 자료의 형식이 올바르지 않습니다.");
      state.detail = detail;
      renderDetail(detail);
    } catch (error) {
      if (request !== state.request) return;
      panel.replaceChildren(emptyList("상세를 표시할 수 없습니다", error.message));
    } finally {
      if (request === state.request) panel.setAttribute("aria-busy", "false");
    }
  }

  function toast(message) {
    clearTimeout(toastTimer);
    $("toast").textContent = message;
    $("toast").hidden = false;
    toastTimer = setTimeout(() => { $("toast").hidden = true; }, 3800);
  }

  async function copyDraft(detail) {
    const text = `[가상 데이터 데모 · 실제 기사/AI 분석 아님]\n${detail.title}\n\n${detail.bullets.map(bullet => `• ${bullet}`).join("\n")}\n\n검토 이유: ${detail.why_now}\n출처: 가상 시나리오\n※ 실제 보고 이력에 저장되지 않은 초안입니다.`;
    try {
      if (!navigator.clipboard || !window.isSecureContext) throw new Error("복사 대체 화면");
      await navigator.clipboard.writeText(text);
      toast("데모 초안을 복사했습니다. 실제 보고 이력에는 저장되지 않습니다.");
    } catch {
      $("copy-text").value = text;
      $("copy-dialog").showModal();
      $("copy-text").focus();
      $("copy-text").select();
    }
  }

  async function loadData() {
    const loadRequest = ++state.loadRequest;
    state.request++;
    state.selected = null;
    state.detail = null;
    state.latest = null;
    state.status = null;
    $("error-banner").hidden = true;
    $("candidate-list").setAttribute("aria-busy", "true");
    $("candidate-list").replaceChildren(emptyList("데모 자료를 불러오고 있습니다", "새로운 목록과 상세 자료를 확인합니다."));
    $("detail-panel").setAttribute("aria-busy", "true");
    $("detail-panel").replaceChildren(emptyList("자료를 갱신하고 있습니다", "이전 이슈 선택을 정리하고 최신 마트를 확인합니다."));
    $("visible-count").textContent = "불러오는 중";
    $("reload-data").disabled = true;
    try {
      const [latest, status, history] = await Promise.all([getJSON("data/latest.json"), getJSON("data/status.json"), getJSON("data/report-history.json")]);
      if (loadRequest !== state.loadRequest) return;
      if (latest.is_demo !== true || status.is_demo !== true || history.is_demo !== true) throw new Error("이 화면은 가상 데이터 데모 전용입니다. 운영 데이터는 표시하지 않습니다.");
      if (!Array.isArray(latest.candidates) || !latest.generation_id || !Array.isArray(history.reports)) throw new Error("마트의 필수 항목이 없거나 형식이 올바르지 않습니다.");
      if ((status.published_generation_id || status.generation_id) !== latest.generation_id || history.generation_id !== latest.generation_id) throw new Error("배포된 자료의 생성 버전이 일치하지 않습니다. 잠시 후 다시 불러와 주세요.");
      if (history.reports.length !== 0 || Number(status.report_count || 0) !== 0) throw new Error("1단계 데모의 실제 보고 이력은 비어 있어야 합니다.");
      if (!latest.candidates.every(item => typeof item.issue_id === "string" && typeof item.title === "string" && typeof item.company === "string" && Object.hasOwn(topicNames, item.topic_id) && Array.isArray(item.bullets))) throw new Error("후보 목록의 형식이 올바르지 않습니다.");
      state.request++;
      state.selected = null;
      state.detail = null;
      state.latest = latest;
      state.status = status;
      renderSummary();
      renderCandidates();
      if (!latest.candidates.length) {
        $("candidate-list").replaceChildren(emptyList("데모 후보가 생성되지 않았습니다", "GitHub Actions의 실행 결과를 확인해주세요."));
        showError("1단계 데모에 필요한 후보가 없습니다. 정상적인 ‘보고할 이슈 없음’ 상태가 아닙니다.");
      }
    } catch (error) {
      if (loadRequest !== state.loadRequest) return;
      state.latest = null;
      state.status = null;
      metric("issue-count", "—", "건");
      metric("article-count", "—", "건");
      metric("version-count", "—", "개");
      $("last-run").textContent = "확인 불가";
      $("run-count").textContent = "—회";
      $("visible-count").textContent = "불러오기 실패";
      $("candidate-list").replaceChildren(emptyList("자료를 불러오지 못했습니다", "아래 실행 기록에서 데모 생성·배포 상태를 확인해주세요."));
      $("detail-panel").replaceChildren(emptyList("데이터 확인이 필요합니다", "자료를 다시 불러오거나 GitHub Actions 실행 기록을 확인해주세요."));
      $("detail-panel").setAttribute("aria-busy", "false");
      showError(error.message);
    } finally {
      if (loadRequest === state.loadRequest) {
        $("candidate-list").setAttribute("aria-busy", "false");
        $("reload-data").disabled = false;
      }
    }
  }

  document.querySelectorAll(".topic-button").forEach(button => button.addEventListener("click", () => {
    state.topic = button.dataset.topic;
    if (state.latest) renderCandidates();
  }));
  $("search").addEventListener("input", renderCandidates);
  $("company-filter").addEventListener("change", renderCandidates);
  $("sort-order").addEventListener("change", renderCandidates);
  $("reset-filters").addEventListener("click", () => {
    state.topic = "";
    $("search").value = "";
    $("company-filter").value = "";
    $("sort-order").value = "priority";
    if (state.latest) renderCandidates();
  });
  $("reload-data").addEventListener("click", loadData);
  $("history-open").addEventListener("click", () => $("history-dialog").showModal());
  $("history-mobile").addEventListener("click", () => $("history-dialog").showModal());
  $("history-close").addEventListener("click", () => $("history-dialog").close());
  $("copy-close").addEventListener("click", () => $("copy-dialog").close());
  for (const dialog of [$("history-dialog"), $("copy-dialog")]) dialog.addEventListener("click", event => { if (event.target === dialog) { const bounds = dialog.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close(); } });
  loadData();
})();
