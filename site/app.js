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
    if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
      const parsed = new Date(`${value}T00:00:00Z`);
      return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value ? value.replaceAll("-", ".") : "날짜 미확인";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "날짜 미확인";
    const parts = new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", ...(includeTime ? { hour: "2-digit", minute: "2-digit", hourCycle: "h23" } : {}) }).formatToParts(date);
    const p = Object.fromEntries(parts.map(part => [part.type, part.value]));
    return `${p.year}.${p.month}.${p.day}${includeTime ? ` · ${p.hour}:${p.minute}` : ""}`;
  }

  function safeSourceURL(value) {
    if (typeof value !== "string" || !/^https:\/\//i.test(value)) return null;
    try {
      const url = new URL(value);
      return url.protocol === "https:" && url.hostname && !url.username && !url.password ? url.href : null;
    } catch { return null; }
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
    $(id).replaceChildren(document.createTextNode(String(value)), el("small", "", unit));
  }

  function renderMode() {
    const demo = state.latest.is_demo;
    const values = {
      "mode-badge": demo ? "DEMO" : "PHASE 02",
      "nav-caption": demo ? "검토할 이슈" : "수집 주제",
      "all-topic-label": demo ? "전체 이슈" : "전체 자료",
      "page-heading": demo ? "이번 주, 살펴볼 변화" : "이번 주, 수집된 자료",
      "page-intro": demo ? "경쟁사의 움직임을 모으고, 보고할 이유를 확인합니다." : "공개 출처의 자료를 모아 원문과 수집 상태를 확인합니다.",
      "notice-title": demo ? "가상 데이터 · 실제 기사/AI 분석 아님" : "공개 자료 수집 · AI 분석 미연결",
      "notice-description": demo ? "수집부터 화면까지의 연결을 확인하는 1단계 데모입니다. 모든 회사와 사건은 가상입니다." : "제목 기준으로 주제를 분류했습니다. 우선순위·선정 이유·보고 초안은 생성하지 않았으며 원문 확인이 필요합니다.",
      "phase-label": demo ? "PHASE 01" : "PHASE 02",
      "issue-count-label": demo ? "검토 후보" : "수집 자료",
      "article-count-label": demo ? "원자료" : "현재 범위 자료",
      "version-count-label": demo ? "보존한 자료 버전" : "현재 범위 자료 버전",
      "last-run-label": demo ? "마지막 데모 실행" : "마지막 수집 시도",
      "list-heading": demo ? "보고 후보" : "수집 자료",
      "review-note": demo ? "최종 보고는 담당자가 결정합니다" : "제목 기준 주제 분류 · 원문 확인 필요",
      "list-kind": demo ? "DEMO SCENARIOS" : "COLLECTED SOURCES",
      "data-flow": demo ? "가상 시나리오 → 원자료 → 마트 → 화면" : "공개 출처 → 메타데이터 → 화면",
      "history-explanation": demo ? "데모 초안 생성이나 복사는 실제 보고로 기록되지 않습니다." : "자료 확인이나 복사는 실제 보고로 기록되지 않습니다."
    };
    Object.entries(values).forEach(([id, value]) => { $(id).textContent = value; });
    $("mode-notice").setAttribute("aria-label", demo ? "데모 안내" : "수집 자료 안내");
    $("execution-mode").textContent = state.status.schedule_enabled === true ? "정기 실행 연결됨" : "수동 실행 모드";
    $("connection-status").textContent = `${state.status.schedule_enabled === true ? "정기 수집 켜짐" : "정기 수집 꺼짐"} · AI 미연결`;
    $("candidate-list").setAttribute("aria-label", demo ? "보고 후보 목록" : "수집 자료 목록");
    const sort = $("sort-order");
    const previous = sort.value;
    sort.replaceChildren(new Option(demo ? "중요도순" : "최신순", demo ? "priority" : "recent"), new Option("제목순", "title"));
    if ([...sort.options].some(option => option.value === previous)) sort.value = previous;
  }

  function renderSummary() {
    const { latest, status } = state;
    renderMode();
    metric("issue-count", latest.candidates.length, "건");
    metric("article-count", status.article_count ?? "—", "건");
    metric("version-count", status.version_count ?? "—", "개");
    $("last-run").textContent = formatDate(status.last_run_at || latest.generated_at, true);
    $("run-count").textContent = `${status.run_count ?? "—"}회`;
    const week = /^(\d{4})-W(\d{1,2})$/.exec(latest.report_week || "");
    $("week-label").textContent = week ? `${week[1]} / WEEK ${week[2]}` : (latest.is_demo ? "DEMO EDITION" : "WEEKLY SOURCES");
    $("week-number").textContent = week ? week[2] : "—";
    $("edition-year").textContent = week ? `${week[1]} · ${latest.is_demo ? "DEMO" : "SOURCES"}` : (latest.is_demo ? "가상 데이터 데모" : "공개 출처 자료");
    $("all-count").textContent = latest.candidates.length;
    document.querySelectorAll("[data-count]").forEach(node => {
      node.textContent = latest.candidates.filter(item => item.topic_id === node.dataset.count).length;
    });
    const companySelect = $("company-filter");
    const previousCompany = companySelect.value;
    companySelect.replaceChildren(new Option("모든 회사", ""));
    [...new Set(latest.candidates.flatMap(item => Array.isArray(item.companies) && item.companies.length ? item.companies : [item.company]))].filter(company => typeof company === "string" && company).sort((a,b) => a.localeCompare(b,"ko")).forEach(company => companySelect.add(new Option(company, company)));
    if ([...companySelect.options].some(option => option.value === previousCompany)) companySelect.value = previousCompany;
    renderSourceStatus();
  }

  function renderSourceStatus() {
    const panel = $("source-status-panel");
    const list = $("source-status-list");
    list.replaceChildren();
    panel.hidden = state.latest.is_demo;
    if (panel.hidden) return;
    const sources = Array.isArray(state.status.sources) ? state.status.sources : [];
    const failed = sources.filter(source => source.status === "failed").length;
    const unchanged = sources.filter(source => source.status === "not_modified").length;
    const success = sources.filter(source => source.status === "success").length;
    const publication = state.status.publication_status;
    $("source-status-summary").textContent = `성공 ${success} · 304 ${unchanged} · 실패 ${failed}${publication === "filtered" ? " · 기존 자료에 변경된 수집 범위 적용" : publication === "retained" ? " · 이전 자료 유지" : publication === "unavailable" ? " · 게시 자료 없음" : ""}`;
    panel.open = failed > 0 || publication === "unavailable" || publication === "filtered";
    if (publication === "filtered") list.append(el("p", "source-alert", `수집 실패 · 기존 자료에 변경된 수집 범위 적용. 이번 수집은 성공하지 않았으며, 기존에 보관한 자료에서 현재 범위에 해당하는 항목만 게시했습니다. 새로 수집된 자료가 아닙니다. 최근 성공 실행: ${formatDate(state.status.last_successful_run_at, true)}.`));
    if (publication === "retained") list.append(el("p", "source-alert", `현재 화면은 마지막으로 게시한 자료를 유지합니다. 현재 자료 생성: ${formatDate(state.latest.generated_at, true)}. 최근 성공 실행: ${formatDate(state.status.last_successful_run_at, true)}.`));
    if (publication === "unavailable") list.append(el("p", "source-alert", "아직 게시할 수 있는 수집 결과가 없습니다. 아래 실패 내역을 확인해주세요. 이 상태는 ‘보고할 이슈 없음’을 뜻하지 않습니다."));
    if (Number(state.status.archived_article_count) > 0) list.append(el("p", "source-coverage", `현재 범위 자료 ${state.status.article_count ?? "—"}건을 표시합니다. 범위에서 제외된 보관 자료 ${state.status.archived_article_count}건은 목록과 현재 집계에 포함하지 않습니다.`));
    if (!sources.length) list.append(el("p", "source-alert", "출처별 수집 상태가 제공되지 않았습니다. 실행 기록을 확인해주세요."));
    const statusNames = { success: "수집 성공", not_modified: "변경 없음 · 304", failed: "수집 실패" };
    sources.forEach(source => {
      const row = el("article", "source-status-item");
      const heading = el("div", "source-status-heading");
      heading.append(el("h3", "", source.source_name || source.source_id || "출처 미확인"), el("span", `source-state${source.status === "failed" ? " failed" : ""}`, statusNames[source.status] || "상태 미확인"));
      const dates = el("p", "source-status-meta", `최근 시도 ${formatDate(source.last_attempt_at, true)} · 최근 성공 ${source.last_success_at ? formatDate(source.last_success_at, true) : "성공 이력 없음"}`);
      const conditionExcluded = source.outside_scope_count == null && source.outside_content_filter_count == null ? "—" : Number(source.outside_scope_count || 0) + Number(source.outside_content_filter_count || 0);
      const counts = el("p", "source-status-meta", `피드 항목 ${source.fetched_count ?? "—"}건 · 조건 통과 ${source.accepted_count ?? "—"}건 · 조건 제외 ${conditionExcluded}건 · 기간 제외 ${source.outside_window_count ?? "—"}건 · 유효성 제외 ${source.rejected_count ?? "—"}건 · 중복 ${source.duplicate_count ?? "—"}건 · 새 버전 ${source.inserted_versions ?? "—"}개`);
      row.append(heading, dates, counts);
      if (source.status === "failed") row.append(el("p", "source-error", [source.error_code, source.error_message || "자세한 내용은 실행 기록을 확인해주세요."].filter(Boolean).join(" · ")));
      if (source.status === "not_modified") row.append(el("p", "source-coverage", "HTTP 304: 출처가 피드 변경 없음으로 응답했습니다. 기존에 저장한 자료를 유지합니다."));
      const range = source.coverage_start || source.coverage_end ? `조회한 날짜 범위 ${formatDate(source.coverage_start)} ~ ${formatDate(source.coverage_end)}. ` : "조회한 날짜 범위 없음. ";
      row.append(el("p", "source-coverage", range + (source.coverage_note || "피드에서 확인 가능한 항목에 한하며 전체 기간 수집을 보장하지 않습니다.")));
      list.append(row);
    });
  }

  function visibleCandidates() {
    if (!state.latest) return [];
    const query = $("search").value.trim().toLocaleLowerCase("ko");
    const company = $("company-filter").value;
    const result = state.latest.candidates.filter(item => (!state.topic || item.topic_id === state.topic) && (!company || item.company === company || (Array.isArray(item.companies) && item.companies.includes(company))) && (!query || [item.title, item.company, item.topic, item.source_name, ...(Array.isArray(item.companies) ? item.companies : []), ...(state.latest.is_demo ? [item.why_now, ...(item.bullets || [])] : [])].join(" ").toLocaleLowerCase("ko").includes(query)));
    const time = item => Date.parse(item.published_at || item.published_date || item.fetched_at || "") || 0;
    return result.sort((a,b) => {
      const titleOrder = a.title.localeCompare(b.title,"ko");
      if ($("sort-order").value === "title") return titleOrder;
      if (!state.latest.is_demo) return time(b) - time(a) || titleOrder;
      return (a.priority === "high" ? 0 : 1) - (b.priority === "high" ? 0 : 1) || titleOrder;
    });
  }

  function renderCandidates() {
    if (!state.latest) return;
    const demo = state.latest.is_demo;
    const items = visibleCandidates();
    const list = $("candidate-list");
    list.replaceChildren();
    $("visible-count").textContent = `${items.length}건`;
    $("active-topic-label").textContent = topicNames[state.topic] || (demo ? "전체 이슈" : "전체 자료");
    document.querySelectorAll(".topic-button").forEach(button => {
      const active = button.dataset.topic === state.topic;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    if (!items.length) {
      list.append(emptyList("조건에 맞는 자료가 없습니다", "검색어나 주제·회사 필터를 바꿔보세요."));
      state.selected = null;
      state.detail = null;
      state.request++;
      $("detail-panel").setAttribute("aria-busy", "false");
      $("detail-panel").replaceChildren(emptyList("선택된 자료가 없습니다", "필터를 초기화하면 전체 목록을 확인할 수 있습니다."));
      return;
    }
    items.forEach(item => {
      const button = el("button", `candidate${state.selected === item.issue_id ? " selected" : ""}`);
      button.type = "button";
      button.dataset.issue = item.issue_id;
      button.setAttribute("aria-pressed", String(state.selected === item.issue_id));
      button.setAttribute("aria-controls", "detail-panel");
      const top = el("div", "candidate-top");
      top.append(el("span", `priority ${demo && item.priority === "high" ? "high" : ""}`, demo ? (item.priority === "high" ? "우선 검토" : "일반 검토") : "분석 대기"), el("span", "company-name", item.company));
      const bottom = el("div", "candidate-bottom");
      bottom.append(el("span", "", item.topic || topicNames[item.topic_id]), el("span", "", demo ? "가상 시나리오" : (item.source_name || "공개 출처")));
      if (!demo && (item.published_at || item.published_date)) bottom.append(el("span", "", formatDate(item.published_at || item.published_date)));
      const arrow = el("span", "candidate-arrow", "↗");
      arrow.setAttribute("aria-hidden", "true");
      button.append(top, el("h3", "", item.title), el("p", "candidate-reason", demo ? item.why_now : "제목 기준 주제 분류 · 원문 확인 필요"), bottom, arrow);
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
    const demo = detail.is_demo;
    panel.replaceChildren();
    const kicker = el("div", "detail-kicker");
    kicker.append(el("span", "", demo ? "ISSUE BRIEF" : "SOURCE RECORD"), el("span", "", demo ? "가상 데이터" : "AI 분석 미연결"));
    const meta = el("div", "detail-meta");
    meta.append(el("span", "", detail.company), el("span", "", "등록된 보고 이력 없음"));
    panel.append(kicker, el("div", "detail-topic", detail.topic), el("h3", "detail-title", detail.title), meta);
    if (demo) {
      const why = detailSection("01", "왜 살펴봐야 하나요");
      why.append(el("p", "reason-box", detail.why_now));
      const draft = detailSection("02", "보고 초안");
      draft.querySelector("h4").append(el("span", "draft-label", "규칙 기반 데모"));
      const bullets = el("ul", "brief-bullets");
      detail.bullets.forEach(text => bullets.append(el("li", "", text)));
      draft.append(bullets, el("p", "report-note", "등록된 실제 보고 이력이 없어 기보고 대비 비교는 제공하지 않습니다. 이 초안은 실제 보고 완료를 의미하지 않습니다."));
      panel.append(why, draft);
    } else {
      const analysis = detailSection("01", "분석 연결 전 수집 자료");
      analysis.append(el("p", "reason-box", "제목 기준 주제 분류만 적용했습니다. AI 분석은 미연결 상태이며, 중요도와 이번 주 보고할 이유는 판단하지 않았습니다."), el("p", "report-note", "원문 확인이 필요합니다. 요약·보고 초안·기보고 대비 비교는 제공하지 않습니다. 최근 경영진 관심사와 실제 보고 이력은 미등록 상태입니다."));
      panel.append(analysis);
    }
    const evidenceSection = detailSection(demo ? "03" : "02", demo ? "확인한 근거" : "출처와 저장 범위");
    if (demo && detail.change_summary) evidenceSection.append(el("p", "change-note", detail.change_summary));
    detail.evidence.forEach(evidence => {
      const article = el("article", "evidence-item");
      const label = el("div", "evidence-label");
      label.append(el("span", "", evidence.source_name || (demo ? "가상 시나리오" : "출처 미확인")), el("span", "", `발행·접수일 ${formatDate(evidence.published_at || evidence.published_date)}`));
      article.append(label, el("p", "evidence-title", evidence.title));
      if (demo) {
        const disclosure = el("details");
        disclosure.append(el("summary", "", "가상 원자료 보기"), el("div", "evidence-text", evidence.content_text || "원자료 본문 없음"));
        article.append(disclosure);
      } else {
        const url = safeSourceURL(evidence.source_url);
        if (url) {
          const link = el("a", "source-link", "원문 확인 ↗");
          link.href = url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.setAttribute("aria-label", `${evidence.title || "자료"} 원문 확인 (새 탭)`);
          article.append(link, el("p", "source-domain", new URL(url).hostname));
        } else article.append(el("p", "source-error", "안전한 HTTPS 원문 링크를 확인할 수 없습니다."));
        article.append(el("p", "evidence-storage", `수집 ${formatDate(evidence.fetched_at, true)} · ${evidence.content_level === "metadata_only" ? "메타데이터만 보관" : "저장 범위 확인 필요"}`), el("p", "evidence-footnote", "공개 저장 범위: 제목·출처·원문 URL·발행·접수일 및 수집 시각. 기사 본문과 이미지, 요약문은 이 화면에 저장하거나 제공하지 않습니다."));
        if (evidence.retention_basis) article.append(el("p", "evidence-footnote", evidence.retention_basis));
      }
      evidenceSection.append(article);
    });
    if (!detail.evidence.length) evidenceSection.append(el("p", "evidence-footnote", "연결된 출처 정보가 없습니다. 실행 기록을 확인해주세요."));
    if (demo) evidenceSection.append(el("p", "evidence-footnote", "근거는 저장소에 포함된 가상 시나리오입니다. 실제 언론 기사나 외부 출처를 인용하지 않았습니다."));
    const actions = el("div", "detail-actions");
    const copy = el("button", "copy-button", demo ? "보고 초안 복사" : "자료 정보 복사");
    copy.type = "button";
    copy.addEventListener("click", () => copyRecord(detail));
    actions.append(copy, el("span", "", demo ? "복사한 내용은 저장되지 않으며, 실제 보고 이력에 반영되지 않습니다." : "제목·출처·URL·날짜만 복사합니다. 실제 보고 이력에는 반영되지 않습니다."));
    panel.append(evidenceSection, actions);
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
    panel.replaceChildren(emptyList("상세 자료를 불러오고 있습니다", "출처와 저장된 자료를 확인합니다."));
    try {
      if (typeof item.detail_path !== "string" || !/^data\/issues\/[A-Za-z0-9_-]+\.json$/.test(item.detail_path)) throw new Error("상세 경로가 올바르지 않습니다.");
      const detail = await getJSON(item.detail_path);
      if (request !== state.request) return;
      if (detail.is_demo !== state.latest.is_demo || detail.issue_id !== item.issue_id || detail.generation_id !== state.latest.generation_id) throw new Error("목록과 상세의 생성 버전이 다릅니다. 자료를 다시 불러와 주세요.");
      if (!Array.isArray(detail.bullets) || !Array.isArray(detail.evidence)) throw new Error("상세 자료의 형식이 올바르지 않습니다.");
      if (!detail.is_demo && (detail.analysis_status !== "pending_ai" || detail.bullets.length !== 0 || detail.evidence.some(evidence => evidence.content_level !== "metadata_only"))) throw new Error("수집 자료의 분석 상태 또는 공개 저장 범위가 예상과 다릅니다. 실행 기록을 확인해주세요.");
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

  async function copyRecord(detail) {
    const demo = detail.is_demo;
    const text = demo ? `[가상 데이터 데모 · 실제 기사/AI 분석 아님]\n${detail.title}\n\n${detail.bullets.map(bullet => `• ${bullet}`).join("\n")}\n\n검토 이유: ${detail.why_now}\n출처: 가상 시나리오\n※ 실제 보고 이력에 저장되지 않은 초안입니다.` : detail.evidence.map(evidence => `제목: ${evidence.title || detail.title}\n출처: ${evidence.source_name || "출처 미확인"}\nURL: ${safeSourceURL(evidence.source_url) || "유효한 HTTPS 링크 없음"}\n발행·접수일: ${formatDate(evidence.published_at || evidence.published_date)}\n수집일: ${formatDate(evidence.fetched_at, true)}`).join("\n\n");
    if (!text) { toast("복사할 출처 정보가 없습니다."); return; }
    try {
      if (!navigator.clipboard || !window.isSecureContext) throw new Error("복사 대체 화면");
      await navigator.clipboard.writeText(text);
      toast(demo ? "데모 초안을 복사했습니다. 실제 보고 이력에는 저장되지 않습니다." : "자료 정보를 복사했습니다. 실제 보고 이력에는 저장되지 않습니다.");
    } catch {
      $("copy-heading").textContent = demo ? "보고 초안 복사" : "자료 정보 복사";
      $("copy-text-label").textContent = demo ? "복사할 데모 보고 초안" : "복사할 자료 정보";
      $("copy-disclaimer").textContent = demo ? "가상 시나리오이며 실제 보고 이력에 저장되지 않습니다." : "제목·출처·URL·날짜만 포함하며 실제 보고 이력에 저장되지 않습니다.";
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
    $("source-status-panel").hidden = true;
    $("candidate-list").setAttribute("aria-busy", "true");
    $("candidate-list").replaceChildren(emptyList("자료를 불러오고 있습니다", "새로운 목록과 상세 자료를 확인합니다."));
    $("detail-panel").setAttribute("aria-busy", "true");
    $("detail-panel").replaceChildren(emptyList("자료를 갱신하고 있습니다", "이전 선택을 정리하고 최신 마트를 확인합니다."));
    $("visible-count").textContent = "불러오는 중";
    $("reload-data").disabled = true;
    try {
      const [latest, status, history] = await Promise.all([getJSON("data/latest.json"), getJSON("data/status.json"), getJSON("data/report-history.json")]);
      if (loadRequest !== state.loadRequest) return;
      if (typeof latest.is_demo !== "boolean" || status.is_demo !== latest.is_demo || history.is_demo !== latest.is_demo) throw new Error("목록·상태·보고 이력의 자료 모드가 일치하지 않습니다.");
      if (!Array.isArray(latest.candidates) || !latest.generation_id || !Array.isArray(history.reports)) throw new Error("마트의 필수 항목이 없거나 형식이 올바르지 않습니다.");
      if ((status.published_generation_id || status.generation_id) !== latest.generation_id || history.generation_id !== latest.generation_id) throw new Error("배포된 자료의 생성 버전이 일치하지 않습니다. 잠시 후 다시 불러와 주세요.");
      if (history.reports.length !== 0 || Number(status.report_count || 0) !== 0 || Number(status.interests_count || 0) !== 0) throw new Error("현재 단계의 실제 보고 이력과 경영진 관심사는 비어 있어야 합니다.");
      if (!latest.candidates.every(item => typeof item.issue_id === "string" && typeof item.title === "string" && typeof item.company === "string" && Object.hasOwn(topicNames, item.topic_id) && Array.isArray(item.bullets))) throw new Error("자료 목록의 형식이 올바르지 않습니다.");
      if (!latest.is_demo && (status.ai_enabled === true || latest.candidates.some(item => item.analysis_status !== "pending_ai" || item.priority !== "unranked" || item.bullets.length !== 0))) throw new Error("현재 단계는 AI 분석 미연결 자료만 표시합니다. 생성 상태를 확인해주세요.");
      state.request++;
      state.selected = null;
      state.detail = null;
      state.latest = latest;
      state.status = status;
      renderSummary();
      renderCandidates();
      if (!latest.candidates.length) {
        if (latest.is_demo) {
          $("candidate-list").replaceChildren(emptyList("데모 후보가 생성되지 않았습니다", "GitHub Actions의 실행 결과를 확인해주세요."));
          showError("데모에 필요한 후보가 없습니다. 정상적인 ‘보고할 이슈 없음’ 상태가 아닙니다.");
        } else {
          const unavailable = status.publication_status === "unavailable";
          const filtered = status.publication_status === "filtered";
          $("candidate-list").replaceChildren(emptyList(filtered ? "변경된 수집 범위에 맞는 기존 자료가 없습니다" : unavailable ? "게시 가능한 수집 결과가 없습니다" : "수집 조건을 통과한 자료가 없습니다", filtered ? "이번 수집은 실패했습니다. 출처별 수집 상태와 실행 기록을 확인해주세요." : unavailable ? "출처별 수집 상태와 실행 기록을 확인해주세요." : "현재 피드 범위와 회사·주제 조건에 해당하는 자료가 없습니다. 출처별 상태를 확인해주세요."));
          $("detail-panel").replaceChildren(emptyList("분석 결과가 아닙니다", "빈 수집 목록은 ‘보고할 이슈 없음’을 의미하지 않습니다. AI 분석은 아직 연결되지 않았습니다."));
        }
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
      $("candidate-list").replaceChildren(emptyList("자료를 불러오지 못했습니다", "실행 기록에서 수집·생성·배포 상태를 확인해주세요."));
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
    $("sort-order").value = state.latest?.is_demo ? "priority" : "recent";
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
