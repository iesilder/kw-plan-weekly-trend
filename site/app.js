"use strict";

(() => {
  const topicNames = {
    performance: "실적·자본·주주정책",
    digital: "신사업·상품·고객전략",
    expansion: "제휴·인수·해외사업",
    strategy: "조직·경영전략",
    risk: "정책·인허가·리스크"
  };
  const $ = id => document.getElementById(id);
  const state = { latest: null, status: null, topic: "", selected: null, detail: null, request: 0, loadRequest: 0 };
  const Review = window.WeeklyReview;
  const AI = window.WeeklyAI;
  const Article = window.WeeklyArticle;
  const Interests = window.WeeklyInterests;
  const Registry = window.WeeklyInterestRegistry;
  const registryState = {client:Registry ? Registry.createClient({fetchImpl:(...args) => fetch(...args)}) : null,live:null,loading:false,warning:"",dirty:false,pending:null,forceQueued:false,freshnessLoad:-1};
  const interestsState = {data:null,published:null,loading:true,warning:"",publishedWarning:"",filter:""};
  const articleState = {data:null,loading:true,warning:""};
  const aiState = {data:null,loading:true,warning:""};
  const reviewState = { canonical: null, draft: null, ready: false, problem: "", storageWarning: "", localInvalid: false, duplicates: {}, detailCache: new Map(), weeklyRequest: 0, weeklyRecords: [], weeklyRange: null, memoryMode: null, unsavedStorage: false };
  let toastTimer;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function topicName(id) {
    if (!Object.hasOwn(topicNames, id)) return null;
    const topics = state.latest?.topics;
    const configured = Array.isArray(topics) ? topics.find(topic => topic && topic.id === id) : null;
    const label = configured?.name ?? configured?.label;
    return typeof label === "string" && label.trim() ? label.trim() : topicNames[id];
  }

  function recordTags(record) {
    return [...new Set((Array.isArray(record.tags) ? record.tags : []).filter(tag => typeof tag === "string" && tag.trim()).map(tag => tag.trim()))];
  }

  function eventStage(record) {
    return typeof record.event_stage === "string" && record.event_stage.trim() ? record.event_stage.trim() : null;
  }

  function classificationLabels(record) {
    const tags = recordTags(record);
    const stage = eventStage(record);
    if (!tags.length && !stage) return null;
    const labels = el("div", "classification-labels");
    labels.setAttribute("aria-label", "제목 기준 태그와 단계");
    labels.append(el("span", "classification-caption", "제목 기준"));
    tags.forEach(tag => labels.append(el("span", "classification-tag", tag)));
    if (stage) labels.append(el("span", "event-stage", `단계: ${stage}`));
    return labels;
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

  function renderMode() {
    const demo = state.latest.is_demo;
    const values = {
      "mode-badge": demo ? "DEMO" : "주경동",
      "nav-caption": demo ? "검토할 이슈" : "수집 주제",
      "all-topic-label": demo ? "전체 이슈" : "전체 자료",
      "page-heading": demo ? "이번 주, 살펴볼 변화" : "이번 주, 보고할 변화",
      "page-intro": demo ? "경쟁사의 움직임을 모으고, 보고할 이유를 확인합니다." : "수집한 자료를 검토하고 주간 보고 후보를 고릅니다.",
      "list-heading": demo ? "보고 후보" : "수집 자료",
      "review-note": demo ? "최종 보고는 담당자가 결정합니다" : "제목 기준 주제 분류 · 원문 확인 필요",
      "list-kind": demo ? "DEMO SCENARIOS" : "COLLECTED SOURCES",
      "data-flow": demo ? "가상 시나리오 → 원자료 → 마트 → 화면" : "공개 출처 → 자료 검토 → 주간 후보",
      "history-explanation": demo ? "데모 초안 생성이나 복사는 실제 보고로 기록되지 않습니다." : "채택·보류·제외 및 복사·내보내기는 실제 보고 완료로 기록되지 않습니다."
    };
    Object.entries(values).forEach(([id, value]) => { $(id).textContent = value; });
    $("execution-mode").textContent = demo ? "가상 자료 검토" : "주경동 콘텐츠 검토";
    $("connection-status").textContent = demo ? "실제 기사와 분석 결과가 아닌 데모입니다" : "실행·분석 현황은 관리페이지에서 확인";
    $("candidate-list").setAttribute("aria-label", demo ? "보고 후보 목록" : "수집 자료 목록");
    const sort = $("sort-order");
    const previous = sort.value;
    sort.replaceChildren(new Option(demo ? "중요도순" : "최신순", demo ? "priority" : "recent"), new Option("제목순", "title"));
    if ([...sort.options].some(option => option.value === previous)) sort.value = previous;
  }

  function renderSummary() {
    const { latest } = state;
    renderMode();
    Object.keys(topicNames).forEach(id => { $("topic-label-" + id).textContent = topicName(id); });
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
    const tagSelect = $("tag-filter");
    const previousTag = tagSelect.value;
    tagSelect.replaceChildren(new Option("모든 태그", ""));
    [...new Set(latest.candidates.flatMap(recordTags))].sort((a,b) => a.localeCompare(b,"ko")).forEach(tag => tagSelect.add(new Option(tag, tag)));
    if ([...tagSelect.options].some(option => option.value === previousTag)) tagSelect.value = previousTag;
  }

  function visibleCandidates() {
    if (!state.latest) return [];
    const query = $("search").value.trim().toLocaleLowerCase("ko");
    const company = $("company-filter").value;
    const tag = $("tag-filter").value;
    const reviewFilter = $("review-filter").value;
    const candidates = Interests ? Interests.filterCandidates(interestsState.data,state.latest.candidates,$("interest-filter").value) : state.latest.candidates;
    const result = candidates.filter(item => (!reviewFilter || (reviewFilter === "duplicates" ? (reviewState.duplicates[item.issue_id] || []).length > 0 : reviewStatus(item.issue_id) === reviewFilter)) && (!state.topic || item.topic_id === state.topic) && (!company || item.company === company || (Array.isArray(item.companies) && item.companies.includes(company))) && (!tag || recordTags(item).includes(tag)) && (!query || [item.title, item.company, topicName(item.topic_id), item.source_name, ...recordTags(item), eventStage(item), ...(Array.isArray(item.companies) ? item.companies : []), ...(state.latest.is_demo ? [item.why_now, ...(item.bullets || [])] : [])].join(" ").toLocaleLowerCase("ko").includes(query)));
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
    const selectedInterest = interestsState.data?.interests.find(item => item.id === $("interest-filter").value);
    $("active-topic-label").textContent = [topicName(state.topic) || (demo ? "전체 이슈" : "전체 자료"),selectedInterest ? `관심사: ${selectedInterest.title}` : null].filter(Boolean).join(" · ");
    document.querySelectorAll(".interest-filter-button").forEach(button => button.setAttribute("aria-pressed",String(button.dataset.interest === $("interest-filter").value)));
    document.querySelectorAll(".topic-button").forEach(button => {
      const active = button.dataset.topic === state.topic;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    if (!items.length) {
      list.append(emptyList("조건에 맞는 자료가 없습니다", "검색어나 주제·회사·태그·관심사·검토 상태 필터를 바꿔보세요."));
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
      if (!demo) {
        top.firstChild.remove();
        top.prepend(reviewBadge(item.issue_id));
        const related = reviewState.duplicates[item.issue_id] || [];
        if (related.length) top.append(el("span", "duplicate-badge", `중복 후보 ${related.length}`));
        const interests = Interests?.matchingInterests(interestsState.data,item.issue_id) || [];
        interests.forEach(interest => top.append(el("span","interest-badge",`관심사 · ${interest.title}`)));
      }
      const bottom = el("div", "candidate-bottom");
      bottom.append(el("span", "", topicName(item.topic_id)), el("span", "", demo ? "가상 시나리오" : (item.source_name || "공개 출처")));
      if (!demo && (item.published_at || item.published_date)) bottom.append(el("span", "", formatDate(item.published_at || item.published_date)));
      const arrow = el("span", "candidate-arrow", "↗");
      arrow.setAttribute("aria-hidden", "true");
      button.append(top, el("h3", "", item.title), el("p", "candidate-reason", demo ? item.why_now : "제목 기준 분류 · 단계는 원문 확인 필요"));
      const labels = classificationLabels(item);
      if (labels) button.append(labels);
      button.append(bottom, arrow);
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
    kicker.append(el("span", "", demo ? "ISSUE BRIEF" : "SOURCE RECORD"), el("span", "", demo ? "가상 데이터" : "원자료 · AI 제안 별도"));
    const meta = el("div", "detail-meta");
    meta.append(el("span", "", detail.company), el("span", "", "등록된 보고 이력 없음"));
    panel.append(kicker, el("div", "detail-topic", topicName(detail.topic_id) || detail.topic), el("h3", "detail-title", detail.title), meta);
    if (!demo) panel.append(reviewControls(detail));
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
      const analysis = detailSection("01", "제목 기준 분류");
      analysis.querySelector("h4").append(el("span", "draft-label", "원문 확인 필요"));
      analysis.append(el("p", "reason-box", detail.why_now || "제목의 표현을 기준으로 주제를 분류했습니다. 분류 근거의 상세 내용은 제공되지 않았습니다."));
      const labels = classificationLabels(detail);
      if (labels) analysis.append(labels);
      else analysis.append(el("p", "classification-note", "제목에서 별도 태그나 단계를 분류하지 않았습니다."));
      analysis.append(el("p", "classification-note", "태그와 단계는 제목에 나타난 표현을 기준으로 합니다. 실제 추진·승인·완료 여부를 확인한 사실이 아닙니다."), el("p", "report-note", "수집 단계의 제목 분류입니다. AI 제안이 있으면 아래에 별도로 표시하며, 이 제목 분류에는 원문 요약·실적 수치·보고 초안·기보고 대비 비교가 포함되지 않습니다. 확보한 기사 본문에 대한 요약은 아래 별도 영역에 표시합니다. 관심사 연결은 제목 키워드 기준이며 실제 보고 이력과는 별개입니다."));
      panel.append(analysis, renderAIDetail(detail), renderArticleDetail(detail));
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
        article.append(el("p", "evidence-storage", `수집 ${formatDate(evidence.fetched_at, true)} · ${evidence.content_level === "metadata_only" ? "메타데이터만 보관" : "저장 범위 확인 필요"}`), el("p", "evidence-footnote", "원자료 저장 범위: 제목·출처·원문 URL·발행·접수일 및 수집 시각. 기사 본문 전체와 이미지는 보관하지 않습니다. 별도 AI 분석 영역에는 짧은 요약과 제한된 원문 근거를 표시할 수 있습니다."));
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
      const detail = await getIssueDetail(item);
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

  function localReviewKey() {
    return `weekly-trend:iesilder/kw-plan-weekly-trend:review:${state.latest?.is_demo ? "demo" : "live"}:v1`;
  }

  function reviewStatus(id) {
    return reviewState.ready ? Review.getStatus(reviewState.canonical, reviewState.draft, id) : "unreviewed";
  }

  function reviewBadge(id) {
    const status = reviewStatus(id);
    return el("span", `review-badge review-${status}`, reviewState.ready ? Review.statusLabels[status] : "선택 확인 중");
  }

  function draftProblem() {
    if (!reviewState.ready) return reviewState.problem || "선택 이력을 불러오는 중입니다.";
    if (reviewState.localInvalid) return "브라우저의 임시선택 형식을 읽지 못했습니다. 백업이 있다면 가져오거나 임시선택을 버린 뒤 다시 선택하세요.";
    if (Review.hasConflict(reviewState.canonical, reviewState.draft)) return "GitHub의 선택 이력이 다른 버전으로 바뀌었습니다. 임시선택은 보존했습니다. 백업한 뒤 임시선택을 버리고 최신 선택에서 다시 검토하세요.";
    const active = new Set(state.latest.candidates.map(item => item.issue_id));
    if (Object.keys(reviewState.draft.changes).some(id => !active.has(id))) return "임시선택에 현재 수집 범위에서 제외된 자료가 있습니다. 백업 후 임시선택을 버리고 현재 자료를 다시 선택하세요.";
    return "";
  }

  function saveLocalDraft() {
    try {
      if (Object.keys(reviewState.draft.changes).length) localStorage.setItem(localReviewKey(), JSON.stringify(reviewState.draft));
      else localStorage.removeItem(localReviewKey());
      reviewState.storageWarning = "";
      reviewState.unsavedStorage = false;
    } catch {
      reviewState.unsavedStorage = true;
      reviewState.storageWarning = "브라우저 저장소를 사용할 수 없습니다. 새로고침 전에 임시선택 백업이나 GitHub 저장을 완료해주세요.";
    }
  }

  function initializeInterests(result) {
    interestsState.published = null;
    interestsState.loading = false;
    interestsState.publishedWarning = "";
    try {
      if (state.latest?.is_demo) throw new Error("가상 데모에는 관심사 기사 수집 결과를 연결하지 않습니다.");
      if (!Interests || result.error) throw new Error("저장된 관심사 등록부를 불러오지 못했습니다.");
      interestsState.published = Interests.validateSidecar(result.value,state.latest);
    } catch (error) { interestsState.publishedWarning = `${error.message} 게시된 기사 연결과 실제 관심사 수집 이력을 확인할 수 없습니다.`; }
    rebuildInterestView();
    ensureRegistryFreshness();
  }

  function ensureRegistryFreshness() {
    if (registryState.live && interestsState.published?.registry.last_success_at && Date.parse(interestsState.published.registry.last_success_at) > Date.parse(registryState.live.fetched_at) && registryState.freshnessLoad !== state.loadRequest) {
      registryState.freshnessLoad = state.loadRequest;
      void refreshLiveInterests({force:true});
    }
  }

  function rebuildInterestView() {
    interestsState.data = interestsState.published;
    let mergeWarning = "";
    if (Registry && registryState.live) {
      try {
        interestsState.data = Registry.mergeRegistry(interestsState.published,registryState.live,state.latest?.is_demo === false ? state.latest : null);
        if (interestsState.data.registry_view_status === "stale") mergeWarning = "게시된 등록 목록이 마지막 화면 조회보다 최신입니다. 새 등록 목록을 확인하는 동안 게시된 목록을 유지합니다.";
      } catch { mergeWarning = "등록 목록과 현재 기사 자료의 연결을 확인하지 못했습니다. 확인 가능한 저장 목록을 유지합니다."; }
    }
    interestsState.warning = [registryState.warning,mergeWarning,interestsState.publishedWarning].filter(Boolean).join(" ");
  }

  function refreshLiveInterests({force = false} = {}) {
    if (registryState.pending) {
      if (force) registryState.forceQueued = true;
      return registryState.pending;
    }
    registryState.loading = true;
    rebuildInterestView(); renderInterests();
    const request = Promise.resolve().then(() => {
      if (!registryState.client) throw new Error("registry_unavailable");
      return registryState.client.load({force});
    }).then(live => {
      registryState.live = live;
      registryState.warning = "";
    }).catch(error => {
      const reason = error?.code === "rate_limited" ? "GitHub 조회 제한으로 지금 등록 목록을 다시 확인하지 못했습니다." : "GitHub의 실시간 등록 목록을 확인하지 못했습니다.";
      registryState.warning = `${reason} ${registryState.live ? "마지막으로 확인한 등록 목록을 유지합니다." : interestsState.published ? "마지막 배포에 저장된 목록을 표시합니다. 새 등록·변경은 아직 확인하지 못했습니다." : "미등록이나 관련 기사 0건으로 판단하지 않습니다. 잠시 후 등록 목록을 새로고침하세요."}`;
    }).finally(() => {
      registryState.loading = false;
      registryState.pending = null;
      rebuildInterestView(); renderInterests();
      if (state.latest?.candidates.length) renderCandidates();
      if (registryState.forceQueued) {
        registryState.forceQueued = false;
        void refreshLiveInterests({force:true});
      } else ensureRegistryFreshness();
    });
    registryState.pending = request;
    return request;
  }

  function refreshRegistryOnReturn() {
    if (document.visibilityState === "hidden") return;
    const force = registryState.dirty;
    registryState.dirty = false;
    void refreshLiveInterests({force});
  }

  function renderInterests() {
    const cards = $("interest-cards"), select = $("interest-filter");
    const previous = interestsState.filter;
    cards.replaceChildren();
    select.replaceChildren(new Option("모든 관심사",""));
    $("interests-warning").textContent = interestsState.warning;
    $("interests-warning").hidden = !interestsState.warning;
    $("interests-refresh").disabled = registryState.loading;
    $("interests-refresh").textContent = registryState.loading ? "등록 목록 확인 중…" : "등록 목록 새로고침";
    if (Interests) {
      $("interests-register").href = Interests.registrationURL();
      $("interests-manage").href = Interests.managementURL();
    }
    const data = interestsState.data;
    if (!data) {
      const loading = interestsState.loading || registryState.loading;
      if (!loading) interestsState.filter = "";
      $("interests-readiness").textContent = loading ? "확인 중" : "등록부 확인 필요";
      $("sidebar-interests").textContent = loading ? "확인 중" : "확인 필요";
      $("interests-status").textContent = loading ? "저장된 관심사를 확인하고 있습니다." : "관심사 목록을 확인할 수 없습니다. 미등록이나 관련 기사 0건으로 판단하지 않습니다.";
      $("interests-registry-checked").textContent = loading ? "GitHub 등록 목록 확인 중" : "등록 목록 확인 시각: 확인할 수 없음";
      $("interests-last-run").textContent = loading ? "실제 관심사 수집 이력 확인 중" : "실제 관심사 수집 이력: 확인할 수 없음";
      cards.append(el("p","interests-empty",loading ? "관심사 등록부를 불러오는 중입니다." : "GitHub의 관심사 관리 화면에서 등록 상태를 확인하세요."));
      select.disabled = true;
      // An unknown registry can still use GitHub's request form; its workflow validates active interests.
      $("interests-collect").disabled = false;
      $("interests-collect").title = "GitHub에서 요청을 제출하면 등록부를 먼저 새로 확인합니다.";
      return;
    }
    const summary = Interests.summary(data);
    const live = data.registration_live === true;
    $("interests-readiness").textContent = live ? registryState.warning ? "최근 등록 목록 유지" : `등록 확인 · 활성 ${summary.activeCount}건` : summary.label;
    $("sidebar-interests").textContent = live || data.registry.status === "success" ? `등록 활성 ${summary.activeCount}건` : "확인 필요";
    $("interests-status").textContent = live ? "GitHub에 제출한 관심사 등록 목록입니다. 등록 확인은 기사 수집 실행과 별개이며, 새 등록·변경한 조건은 수집 대기로 표시합니다." : `${summary.note} 현재 표시한 등록 목록은 마지막 배포의 저장본입니다. 새 등록·변경 여부는 GitHub 조회 후 확인합니다.`;
    $("interests-registry-checked").textContent = live ? `${registryState.warning ? "마지막으로 확인한" : "최근 확인한"} 등록 목록 ${formatDate(data.registry_fetched_at,true)} · 한국 시간` : data.registry.last_success_at ? `저장된 등록부 확인 ${formatDate(data.registry.last_success_at,true)} · 최신 GitHub 등록 목록 확인 전` : "최신 GitHub 등록 목록 확인 전";
    $("interests-collect").disabled = false;
    $("interests-collect").title = "GitHub에서 요청을 제출하면 등록부를 먼저 새로 확인한 뒤 별도로 수집합니다.";
    const run = data.last_collection;
    if (run) {
      const runLabels = {success:"수집 완료",partial_success:"일부 출처 수집 실패",failed:"수집 실패",skipped:"수집 생략"};
      $("interests-last-run").replaceChildren(document.createTextNode(`최근 관심사 ${run.mode === "manual" ? "별도 요청" : "일반 수집"} · ${runLabels[run.status]} · ${formatDate(run.completed_at,true)} (한국 시간)`));
      if (run.request_url) {
        const link = el("a","interest-request-link"," 요청 확인 ↗");
        link.href = run.request_url; link.target = "_blank"; link.rel = "noopener noreferrer";
        $("interests-last-run").append(link);
      }
    } else $("interests-last-run").textContent = interestsState.published ? "아직 실제 관심사 수집 이력이 없습니다. 등록만으로 수집 완료로 표시하지 않습니다." : "실제 관심사 수집 이력 확인 전입니다. GitHub 등록 확인은 기사 수집 완료를 의미하지 않습니다.";
    if (!data.interests.length) cards.append(el("p","interests-empty",live ? "GitHub에서 확인한 등록 관심사가 없습니다. 위 ‘관심사 등록’에서 제출한 뒤 이 화면으로 돌아오면 다시 확인합니다." : data.registry.status === "success" ? "마지막 저장본에 등록된 관심사가 없습니다. 새 등록 여부는 GitHub 조회 후 확인합니다." : "확인된 관심사 정보가 없습니다. GitHub에서 등록 상태를 확인하세요."));
    const order = {active:0,disabled:1,expired:2};
    const filterableIds = new Set();
    [...data.interests].sort((a,b) => order[a.state]-order[b.state] || a.title.localeCompare(b.title,"ko")).forEach(item => {
      const registrationPending = ["pending","changed"].includes(item.registration_status);
      const filterable = item.state === "active" && !registrationPending && !!state.latest?.candidates.length;
      if (filterable) { select.add(new Option(item.title,item.id)); filterableIds.add(item.id); }
      const card = el("article",`interest-card interest-${item.state}`);
      const heading = el("div","interest-card-head");
      const registrationLabel = item.registration_status === "pending" ? "등록됨 · 수집 대기" : item.registration_status === "changed" ? "변경됨 · 수집 대기" : item.state === "active" ? "활성" : item.state === "expired" ? "기간 종료" : "사용 안 함 · 시작 전";
      heading.append(el("h3","",item.title),el("span",`interest-state${registrationPending ? " interest-registration-pending" : ""}`,registrationLabel));
      card.append(heading,el("p","interest-question",item.question));
      const keywords = el("div","interest-keywords");
      keywords.setAttribute("aria-label","제목 일치 키워드");
      item.keywords.forEach(word => keywords.append(el("span","",word)));
      card.append(keywords);
      if (item.exclude_keywords.length) card.append(el("p","interest-exclusions",`제외어: ${item.exclude_keywords.join(", ")}`));
      card.append(el("p","interest-period",`${topicName(item.topic_id)} · ${formatDate(item.active_from)} ~ ${item.active_until ? formatDate(item.active_until) : "종료일 없음"}`));
      const complete = ["success","partial_success"].includes(item.collection_status);
      const matchText = item.state !== "active" ? "현재 수집 대상에서 제외되어 있습니다." : registrationPending ? "현재 등록 조건으로 확인된 기사 수집 결과가 없습니다. 관련 기사 없음으로 판단하지 않습니다." : !state.latest ? "기사 자료를 불러오지 못해 관련 기사 연결을 확인할 수 없습니다." : complete ? `현재 연결 기사 ${item.match_count}건${item.collection_status === "partial_success" ? " · 일부 출처를 확인하지 못했습니다." : " · 승인된 RSS 제목 기준"}` : `${Interests.statusLabels[item.collection_status]} · ${item.match_count ? `현재 연결 기사 ${item.match_count}건` : "관련 기사 없음으로 판단하지 않습니다."}`;
      card.append(el("p","interest-match-status",matchText),el("p","interest-collected-at",item.last_collected_at ? `최근 성공 수집 ${formatDate(item.last_collected_at,true)} · ${Interests.statusLabels[item.collection_status]}` : `현재 조건의 수집 완료 이력: 확인되지 않음 · ${Interests.statusLabels[item.collection_status]}`));
      const actions = el("div","interest-card-actions");
      const source = el("a","interest-source-link","GitHub 등록 내용 ↗");
      source.href = item.source_url; source.target = "_blank"; source.rel = "noopener noreferrer";
      source.addEventListener("click",() => { registryState.dirty = true; });
      actions.append(source);
      if (filterable) {
        const filter = el("button","secondary-button interest-filter-button",!complete && !item.match_count ? "연결 기사 보기" : `관련 기사 보기 (${item.match_count})`);
        filter.type = "button"; filter.dataset.interest = item.id;
        filter.setAttribute("aria-pressed",String(previous === item.id));
        filter.addEventListener("click",() => { select.value = item.id; interestsState.filter = item.id; renderCandidates(); $("list-heading").scrollIntoView({behavior:"smooth",block:"start"}); });
        actions.append(filter);
      }
      card.append(actions);
      cards.append(card);
    });
    select.value = filterableIds.has(previous) ? Interests.normalizeFilter(data,previous) : "";
    interestsState.filter = select.value;
    select.disabled = filterableIds.size === 0;
  }

  function initializeAI(result) {
    aiState.data = null;
    aiState.loading = false;
    aiState.warning = "";
    if (state.latest?.is_demo) { aiState.warning = "가상 데모에는 실제 AI 분석 결과를 연결하지 않습니다."; return; }
    try {
      if (!AI) throw new Error("AI 보조 화면 파일을 불러오지 못했습니다.");
      if (result.error) throw new Error("저장된 AI 보조 자료를 불러오지 못했습니다.");
      aiState.data = AI.validateSidecar(result.value,state.latest);
    } catch(error) { aiState.warning = `${error.message} 수집 자료와 검토 선택 기능은 계속 사용할 수 있습니다.`; }
  }

  function initializeArticles(result) {
    articleState.data = null;
    articleState.loading = false;
    articleState.warning = "";
    if (state.latest?.is_demo) { articleState.warning = "가상 데모에는 실제 기사 본문 요약을 연결하지 않습니다."; return; }
    try {
      if (!Article || result.error) throw new Error("저장된 기사 본문 요약을 불러오지 못했습니다.");
      articleState.data = Article.validateSidecar(result.value,state.latest);
    } catch(error) { articleState.warning = `${error.message} 제목 분석과 검토 선택은 계속 이용할 수 있습니다.`; }
  }

  function renderArticleDetail(detail) {
    const section = el("section","article-detail");
    const header = el("div","ai-detail-head");
    header.append(el("h4","","기사 요약 · 개조식"));
    section.append(header);
    if (!articleState.data) {
      header.append(el("span","ai-state-label",articleState.loading ? "확인 중" : "결과 없음"));
      section.append(el("p","ai-detail-message",articleState.loading ? "저장된 기사 본문 분석 상태를 확인하고 있습니다." : articleState.warning || "아직 기사 본문 요약이 없습니다. 제목 분석과 원문 링크를 이용하세요."));
      return section;
    }
    const item = articleState.data.items[detail.issue_id];
    if (!item || !Article.matchesDetail(item,detail)) {
      header.append(el("span","ai-state-label","원문 연결 확인 필요"));
      section.append(el("p","ai-detail-message","현재 원자료의 근거와 본문 요약의 출처가 일치하지 않아 결과를 표시하지 않습니다."));
      return section;
    }
    header.append(el("span","ai-state-label",Article.statusLabels[item.status]));
    section.append(el("p","ai-detail-message",Article.itemMessage(item)));
    if (item.status!=="completed") return section;
    const result = item.result;
    const reference = ids => ids.map(id=>`[${id}]`).join(" ");
    const list = el("ul","article-facts article-summary");
    [result.key_change,...result.facts].forEach(point=>list.append(el("li","",`${point.text} ${reference(point.evidence_ids)}`)));
    section.append(el("h5","article-field-heading",item.truncated ? "기사 일부 요약 · 개조식" : "기사 전체 요약 · 개조식"),list);
    const review = el("div","article-review");
    review.append(el("h5","article-field-heading","AI 검토 의견 · 기사 요약과 별도"));
    const stage = el("p","article-stage");
    stage.append(el("span","","진행 단계 · AI 분류"),el("strong","",result.stage));
    review.append(stage,el("h5","article-field-heading","보고 후보 이유"),el("p","article-inference",result.why_candidate));
    section.append(review);
    const evidence = el("details","article-evidence");
    evidence.append(el("summary","","참조 문단 발췌 보기"));
    result.evidence.forEach(row=>{
      const quote = el("blockquote","article-quote");
      quote.append(el("span","article-paragraph",`참조 문단 발췌 ${row.id}`),el("p","",`“${row.quote}”`));
      evidence.append(quote);
    });
    section.append(evidence);
    const provenance = el("p","ai-provenance");
    const link = el("a","article-source-link",`${item.source_name} 원문 ↗`);
    link.href = Article.articleURL(item.source_url,item.source_name); link.target = "_blank"; link.rel = "noopener noreferrer";
    provenance.append(link,document.createTextNode(` · 본문 확인 ${formatDate(item.body_fetched_at,true)} · 분석 ${formatDate(item.analyzed_at,true)}${item.truncated ? " · 일부 본문 분석" : ""}`));
    section.append(provenance,el("p","ai-advisory-note","출처에 실린 내용의 AI 요약이며 사실관계를 독립적으로 검증한 결과는 아닙니다. 보고 후보 채택은 담당자가 결정하며 이 요약은 목록 복사·CSV에 자동 포함되지 않습니다."));
    return section;
  }

  function renderAIDetail(detail) {
    const section = el("section", "ai-detail");
    const header = el("div", "ai-detail-head");
    header.append(el("h4", "", "AI 검토 보조"));
    section.append(header);
    if (!aiState.data) {
      header.append(el("span", "ai-state-label", aiState.loading ? "확인 중" : "결과 표시 안 함"));
      section.append(el("p", "ai-detail-message", aiState.loading ? "저장된 AI 분석 상태를 확인하고 있습니다." : aiState.warning || "현재 자료에 대한 AI 보조 결과를 확인할 수 없습니다."));
      return section;
    }
    const item = aiState.data.items[detail.issue_id];
    if (!item) {
      header.append(el("span", "ai-state-label", "결과 없음"));
      section.append(el("p", "ai-detail-message", "현재 자료와 연결되는 AI 보조 결과가 없습니다."));
      return section;
    }
    header.append(el("span", "ai-state-label", AI.statusLabels[item.status]));
    section.append(el("p", "ai-detail-message", AI.itemMessage(item)));
    if (item.status !== "completed") return section;
    const suggestion = item.result;
    const fields = el("dl", "ai-detail-fields");
    for (const [label,value] of [["제안 주제",topicName(suggestion.suggested_topic_id)],["관련성",AI.relevanceLabels[suggestion.relevance]],["검토 근거",suggestion.rationale]]) fields.append(el("dt","",label),el("dd","",value));
    section.append(fields);
    const checks = el("div", "ai-checkpoints");
    checks.append(el("h5", "", "원문에서 확인할 사항"));
    if (suggestion.check_points.length) {
      const list = el("ul");
      suggestion.check_points.forEach(text => list.append(el("li", "", text)));
      checks.append(list);
    } else checks.append(el("p", "ai-detail-message", "별도로 제안된 확인 사항은 없습니다. 원문 확인을 마쳤다는 의미는 아닙니다."));
    section.append(checks);
    if (suggestion.duplicate_issue_ids.length) {
      const duplicates = el("div", "ai-duplicates");
      duplicates.append(el("h5", "", "AI가 제안한 중복 검토 대상"),el("p", "ai-advisory-note", "같은 사건인지 담당자가 확인하세요. 원문을 합치거나 검토 상태를 함께 변경하지 않습니다."));
      suggestion.duplicate_issue_ids.forEach(id => {
        const target = state.latest.candidates.find(candidate => candidate.issue_id === id);
        if (!target) return;
        const button = el("button", "duplicate-link", target.title);
        button.type = "button";
        button.addEventListener("click", () => openCandidate(id));
        duplicates.append(button);
      });
      section.append(duplicates);
    }
    section.append(el("p", "ai-advisory-note", "AI 제안은 참고용입니다. 보고 후보 채택 여부와 원문 사실 확인은 담당자가 결정합니다. 이 내용은 주간 후보 복사·CSV에 자동으로 포함되지 않습니다."),el("p", "ai-provenance", `분석 ${formatDate(item.analyzed_at,true)} · ${aiState.data.model} · ${aiState.data.prompt_version}`));
    return section;
  }

  function initializeReviews(result) {
    reviewState.ready = false;
    reviewState.problem = "";
    reviewState.storageWarning = "";
    reviewState.localInvalid = false;
    reviewState.duplicates = {};
    try {
      if (!Review) throw new Error("검토 기능 파일을 불러오지 못했습니다. 새로고침해주세요.");
      if (state.latest.is_demo) throw new Error("가상 데모 자료는 실제 보고 후보로 저장하지 않습니다.");
      if (result.error) throw new Error(`GitHub 선택 이력을 불러오지 못했습니다. ${result.error.message}`);
      reviewState.canonical = Review.validateCanonical(result.value);
      let local = null;
      let storageUnavailable = false;
      try { local = localStorage.getItem(localReviewKey()); }
      catch { storageUnavailable = true; reviewState.storageWarning = "브라우저 임시 저장소에 접근할 수 없습니다. 선택 후 백업이나 GitHub 저장을 해주세요."; }
      const memoryOnly = reviewState.unsavedStorage && reviewState.memoryMode === localReviewKey() && reviewState.draft;
      if (memoryOnly) reviewState.storageWarning = "브라우저에 기록하지 못한 임시선택을 현재 화면에 보존하고 있습니다. 새로고침 전에 백업이나 GitHub 저장을 완료해주세요.";
      try { reviewState.draft = memoryOnly ? Review.validateDraft(reviewState.draft) : local ? Review.validateDraft(JSON.parse(local)) : storageUnavailable && reviewState.memoryMode === localReviewKey() && reviewState.draft ? Review.validateDraft(reviewState.draft) : Review.createDraft(reviewState.canonical); }
      catch { reviewState.localInvalid = true; reviewState.draft = Review.createDraft(reviewState.canonical); }
      reviewState.memoryMode = localReviewKey();
      const reconciled = Review.reconcileDraft(reviewState.canonical, reviewState.draft);
      reviewState.draft = reconciled.draft;
      reviewState.ready = true;
      reviewState.duplicates = Review.findDuplicateCandidates(state.latest.candidates);
      if (reconciled.acknowledged && !reviewState.localInvalid) saveLocalDraft();
      if (!$("week-start").value || !$("week-end").value) setWeekRange(Review.weekRange());
    } catch (error) { reviewState.problem = error.message; }
    renderReviewSummary();
  }

  function renderReviewSummary() {
    const counters = $("review-counters");
    counters.replaceChildren();
    const labels = { unreviewed: "미검토", accepted: "채택", held: "보류", excluded: "제외" };
    for (const [status, label] of Object.entries(labels)) {
      const count = reviewState.ready ? state.latest.candidates.filter(item => reviewStatus(item.issue_id) === status).length : "—";
      const button = el("button", `review-counter review-counter-${status}`);
      button.type = "button";
      button.disabled = !reviewState.ready;
      button.append(el("span", "", label), el("strong", "", count));
      button.setAttribute("aria-label", `${label} ${count}건 보기`);
      button.addEventListener("click", () => { $("review-filter").value = status; renderCandidates(); $("list-heading").scrollIntoView({behavior:"smooth", block:"start"}); });
      counters.append(button);
    }
    const count = reviewState.ready ? Object.keys(reviewState.draft.changes).length : 0;
    const problem = draftProblem();
    $("review-storage-status").textContent = reviewState.ready ? `GitHub 저장 버전 ${reviewState.canonical.revision} · ${count ? `이 브라우저의 미저장 변경 ${count}건` : "GitHub에 저장된 선택과 일치"}` : (reviewState.problem || "선택 이력을 불러오는 중입니다.");
    $("review-alert").textContent = [problem, reviewState.storageWarning].filter(Boolean).join(" ");
    $("review-alert").hidden = !problem && !reviewState.storageWarning;
    $("review-save").disabled = !reviewState.ready || !!problem || !count;
    $("review-backup").disabled = !reviewState.ready || reviewState.localInvalid || !count;
    $("review-import").disabled = !reviewState.ready;
    $("review-discard").hidden = !reviewState.ready || (!count && !reviewState.localInvalid);

  }

  function reviewControls(detail) {
    const box = el("section", "review-controls");
    const title = el("div", "review-controls-title");
    title.append(el("h4", "", "보고 후보 선택"), reviewBadge(detail.issue_id));
    box.append(title);
    const options = el("div", "review-options");
    options.setAttribute("aria-label", "이 자료의 검토 상태");
    for (const [status, label] of Object.entries({accepted:"채택",held:"보류",excluded:"제외",unreviewed:"미검토로 되돌리기"})) {
      const button = el("button", `review-option review-option-${status}`, label);
      button.type = "button";
      button.dataset.reviewStatus = status;
      button.setAttribute("aria-pressed", String(reviewStatus(detail.issue_id) === status));
      button.disabled = !reviewState.ready || !!draftProblem();
      button.addEventListener("click", () => changeDecision(detail.issue_id, status));
      options.append(button);
    }
    box.append(options, el("p", "", "선택은 브라우저에 임시 저장됩니다. 다른 기기에서도 보려면 ‘GitHub에 저장’을 완료하세요."));
    const related = reviewState.duplicates[detail.issue_id] || [];
    if (related.length) {
      const duplicateBox = el("div", "duplicate-review");
      duplicateBox.append(el("h4", "", `같은 사건인지 확인할 자료 ${related.length}건`), el("p", "", "제목이 비슷한 중복 후보입니다. 자동 병합하거나 선택을 함께 변경하지 않습니다."));
      related.forEach(match => {
        const item = state.latest.candidates.find(candidate => candidate.issue_id === match.issue_id);
        if (!item) return;
        const button = el("button", "duplicate-link", item.title);
        button.type = "button";
        button.addEventListener("click", () => openCandidate(item.issue_id));
        duplicateBox.append(button, el("small", "", match.reason));
      });
      box.append(duplicateBox);
    }
    return box;
  }

  function changeDecision(id, status) {
    if (draftProblem()) { toast(draftProblem()); return; }
    try {
      reviewState.draft = Review.setDecision(reviewState.canonical, reviewState.draft, id, status);
      saveLocalDraft();
      renderReviewSummary();
      const detail = state.detail;
      renderCandidates();
      if (detail && state.selected === detail.issue_id) renderDetail(detail);
      renderWeekly();
      toast(`${Review.statusLabels[status]} 상태로 변경했습니다. ${reviewState.storageWarning ? "브라우저 저장이 불가능해 백업이 필요합니다." : "브라우저 임시선택에 반영했습니다."}`);
    } catch (error) { toast(error.message); }
  }

  function openCandidate(id) {
    const item = state.latest?.candidates.find(candidate => candidate.issue_id === id);
    if (!item) return;
    state.topic = "";
    ["search", "company-filter", "tag-filter", "interest-filter", "review-filter"].forEach(key => { $(key).value = ""; });
    interestsState.filter = "";
    state.selected = id;
    renderCandidates();
    selectIssue(item);
    $("detail-panel").scrollIntoView({behavior:"smooth",block:"start"});
  }

  async function getIssueDetail(item) {
    if (typeof item.detail_path !== "string" || !/^data\/issues\/[A-Za-z0-9_-]+\.json$/.test(item.detail_path)) throw new Error("상세 경로가 올바르지 않습니다.");
    const generation = state.latest.generation_id;
    const key = `${generation}:${item.issue_id}`;
    if (reviewState.detailCache.has(key)) return reviewState.detailCache.get(key);
    const promise = getJSON(item.detail_path).then(detail => {
      if (detail.issue_id !== item.issue_id || detail.generation_id !== generation || detail.is_demo !== item.is_demo || !Array.isArray(detail.evidence)) throw new Error("목록과 상세의 생성 버전이 다릅니다. 자료를 다시 불러와 주세요.");
      if (!detail.is_demo && detail.evidence.some(record => record.content_level !== "metadata_only")) throw new Error("공개 저장 범위를 확인할 수 없습니다.");
      return detail;
    }).catch(error => { reviewState.detailCache.delete(key); throw error; });
    reviewState.detailCache.set(key, promise);
    return promise;
  }

  function setWeekRange(range) {
    $("week-start").value = range.start;
    $("week-end").value = range.end;
  }

  function selectedRange() {
    const start = $("week-start").value;
    const end = $("week-end").value;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end) || Review.kstDate(start) !== start || Review.kstDate(end) !== end || start > end) throw new Error("시작일과 종료일을 확인해주세요. 종료일은 시작일보다 빠를 수 없습니다.");
    return {start,end};
  }

  async function renderWeekly() {
    const request = ++reviewState.weeklyRequest;
    const list = $("weekly-list");
    $("weekly-copy").disabled = true;
    $("weekly-csv").disabled = true;
    reviewState.weeklyRecords = [];
    reviewState.weeklyRange = null;
    $("sidebar-accepted").textContent = "—";
    if (!reviewState.ready) {
      $("weekly-count").textContent = "—";
      list.replaceChildren(el("p", "weekly-empty", reviewState.problem || "선택 이력을 불러오는 중입니다."));
      return;
    }
    try {
      const range = selectedRange();
      const accepted = state.latest.candidates.filter(item => reviewStatus(item.issue_id) === "accepted");
      const selected = accepted.filter(item => Review.inDateRange(item, range.start, range.end));
      const missingDate = accepted.filter(item => !Review.publicationDate(item)).length;
      $("weekly-count").textContent = `${selected.length}건`;
      $("weekly-note").textContent = `발행·접수일 ${formatDate(range.start)} ~ ${formatDate(range.end)} · 한국 시간 기준. 검색·필터와 독립된 채택 목록입니다.${missingDate ? ` 발행일 미확인 채택 자료 ${missingDate}건은 기간 목록에서 제외했습니다.` : ""}${draftProblem() ? " 저장 충돌이 있는 임시선택을 포함하므로 먼저 선택 상태를 확인해주세요." : ""}`;
      if (!selected.length) {
        $("sidebar-accepted").textContent = "0건 ↗";
        list.replaceChildren(el("p", "weekly-empty", accepted.length ? "선택한 기간에 채택한 자료가 없습니다. 기간을 바꾸거나 다른 자료를 채택하세요." : "아직 채택한 자료가 없습니다. 위에서 원문을 확인하고 ‘채택’을 눌러보세요."));
        return;
      }
      list.replaceChildren(el("p", "weekly-empty", "채택한 자료의 원문 링크를 확인하고 있습니다…"));
      const details = await Promise.all(selected.map(getIssueDetail));
      if (request !== reviewState.weeklyRequest) return;
      const records = details.map((detail,index) => ({...selected[index], evidence:detail.evidence, topic:topicName(detail.topic_id) || detail.topic}));
      const missingLinks = records.filter(record => !record.evidence.length || record.evidence.some(evidence => !safeSourceURL(evidence.source_url))).length;
      list.replaceChildren();
      for (const id of Object.keys(topicNames)) {
        const topicRecords = records.filter(record => record.topic_id === id);
        if (!topicRecords.length) continue;
        const group = el("section", "weekly-topic");
        group.append(el("h3", "", `${topicName(id)} · ${topicRecords.length}건`));
        const companies = [...new Set(topicRecords.map(record => record.company))].sort((a,b) => a.localeCompare(b,"ko"));
        companies.forEach(company => {
          const companyGroup = el("div", "weekly-company");
          companyGroup.append(el("h4", "", company));
          topicRecords.filter(record => record.company === company).sort((a,b) => (Review.publicationDate(b) || "").localeCompare(Review.publicationDate(a) || "") || a.title.localeCompare(b.title,"ko")).forEach(record => {
            const row = el("article", "weekly-item");
            const title = el("button", "weekly-item-title", record.title);
            title.type = "button";
            title.addEventListener("click", () => openCandidate(record.issue_id));
            row.append(title, el("p", "weekly-item-meta", `${formatDate(Review.publicationDate(record))} · ${record.source_name || "출처 미확인"}`));
            const urls = [...new Set(record.evidence.map(evidence => safeSourceURL(evidence.source_url)).filter(Boolean))];
            urls.forEach((url,index) => {
              const link = el("a", "weekly-source-link", `원문${urls.length > 1 ? ` ${index+1}` : ""} ↗`);
              link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer";
              row.append(link);
            });
            if (!urls.length) row.append(el("span", "source-error", "유효한 원문 링크 없음"));
            if ((reviewState.duplicates[record.issue_id] || []).some(match => records.some(other => other.issue_id === match.issue_id))) row.append(el("span", "weekly-duplicate-note", "채택 목록에 중복 후보가 있습니다 · 개별 원문 유지"));
            companyGroup.append(row);
          });
          group.append(companyGroup);
        });
        list.append(group);
      }
      reviewState.weeklyRecords = records;
      reviewState.weeklyRange = range;
      $("sidebar-accepted").textContent = `${records.length}건 ↗`;
      if (missingLinks) $("weekly-note").textContent += ` 원문 링크를 확인할 수 없는 자료 ${missingLinks}건이 있어 복사·내보내기를 중지했습니다. 자료를 다시 불러오거나 해당 자료의 채택을 해제하세요.`;
      $("weekly-copy").disabled = !!draftProblem() || missingLinks > 0;
      $("weekly-csv").disabled = !!draftProblem() || missingLinks > 0;
    } catch(error) {
      if (request !== reviewState.weeklyRequest) return;
      $("weekly-count").textContent = "확인 필요";
      list.replaceChildren(el("p", "weekly-empty", error.message));
    }
  }

  async function copyText(text, title, notice) {
    try {
      if (!navigator.clipboard || !window.isSecureContext) throw new Error("fallback");
      await navigator.clipboard.writeText(text);
      toast(`${title} 내용을 복사했습니다.`);
    } catch {
      $("copy-heading").textContent = title;
      $("copy-text-label").textContent = title;
      $("copy-disclaimer").textContent = notice;
      $("copy-text").value = text;
      $("copy-dialog").showModal();
      $("copy-text").focus(); $("copy-text").select();
    }
  }

  function downloadText(text, filename, type) {
    const url = URL.createObjectURL(new Blob([text], {type}));
    const link = document.createElement("a");
    link.href = url; link.download = filename;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function openSaveDialog() {
    try {
      if (draftProblem()) throw new Error(draftProblem());
      const patch = Review.serializePatch(reviewState.canonical, reviewState.draft);
      Review.validatePatch(patch, state.latest.candidates.map(item => item.issue_id));
      $("review-patch").value = JSON.stringify(patch,null,2);
      $("review-save-dialog").showModal();
    } catch(error) { toast(error.message); }
  }

  function importDraft() {
    try {
      if (!reviewState.ready) throw new Error("먼저 GitHub 선택 이력을 불러오세요.");
      const text = $("review-import-text").value;
      if (text.length > 65536) throw new Error("백업 내용이 너무 큽니다.");
      const patch = Review.validatePatch(JSON.parse(text), state.latest.candidates.map(item => item.issue_id));
      const imported = Review.draftFromPatch(patch);
      const reconciled = Review.reconcileDraft(reviewState.canonical, imported);
      if (reconciled.conflict) throw new Error("백업의 저장 버전과 현재 GitHub 버전이 다릅니다. 내용을 확인한 뒤 현재 화면에서 다시 선택해주세요.");
      reviewState.draft = reconciled.draft;
      reviewState.localInvalid = false;
      saveLocalDraft();
      renderReviewSummary(); renderCandidates();
      if (state.detail) renderDetail(state.detail);
      renderWeekly();
      $("review-import-dialog").close();
      toast("백업을 브라우저 임시선택으로 가져왔습니다. GitHub 저장은 별도입니다.");
    } catch(error) {
      $("review-import-error").textContent = error instanceof SyntaxError ? "JSON 형식을 읽을 수 없습니다. 백업 파일 내용을 그대로 붙여넣으세요." : error.message;
      $("review-import-error").hidden = false;
    }
  }

  async function loadData({forceRegistry = false} = {}) {
    const loadRequest = ++state.loadRequest;
    state.request++;
    state.selected = null;
    state.detail = null;
    state.latest = null;
    state.status = null;
    reviewState.ready = false;
    reviewState.weeklyRequest++;
    reviewState.detailCache.clear();
    aiState.data = null; aiState.loading = true; aiState.warning = "";
    articleState.data = null; articleState.loading = true; articleState.warning = "";
    interestsState.published = null; interestsState.loading = true; interestsState.publishedWarning = "";
    rebuildInterestView(); renderInterests();
    void refreshLiveInterests({force:forceRegistry});
    renderReviewSummary();
    renderWeekly();
    $("error-banner").hidden = true;
    $("candidate-list").setAttribute("aria-busy", "true");
    $("candidate-list").replaceChildren(emptyList("자료를 불러오고 있습니다", "새로운 목록과 상세 자료를 확인합니다."));
    $("detail-panel").setAttribute("aria-busy", "true");
    $("detail-panel").replaceChildren(emptyList("자료를 갱신하고 있습니다", "이전 선택을 정리하고 최신 마트를 확인합니다."));
    $("visible-count").textContent = "불러오는 중";
    $("reload-data").disabled = true;
    const aiResult = getJSON("data/ai-analysis.json").then(value => ({value})).catch(error => ({error}));
    const articleResult = getJSON("data/article-analysis.json").then(value => ({value})).catch(error => ({error}));
    const interestsResult = getJSON("data/interests.json").then(value => ({value})).catch(error => ({error}));
    try {
      const [latest, status, history, reviewsResult] = await Promise.all([getJSON("data/latest.json"), getJSON("data/status.json"), getJSON("data/report-history.json"), getJSON("data/reviews.json").then(value => ({value})).catch(error => ({error}))]);
      if (loadRequest !== state.loadRequest) return;
      if (typeof latest.is_demo !== "boolean" || status.is_demo !== latest.is_demo || history.is_demo !== latest.is_demo) throw new Error("목록·상태·보고 이력의 자료 모드가 일치하지 않습니다.");
      if (!Array.isArray(latest.candidates) || !latest.generation_id || !Array.isArray(history.reports)) throw new Error("마트의 필수 항목이 없거나 형식이 올바르지 않습니다.");
      if ((status.published_generation_id || status.generation_id) !== latest.generation_id || history.generation_id !== latest.generation_id) throw new Error("배포된 자료의 생성 버전이 일치하지 않습니다. 잠시 후 다시 불러와 주세요.");
      if (history.reports.length !== 0 || Number(status.report_count || 0) !== 0) throw new Error("현재 단계의 실제 보고 이력은 비어 있어야 합니다.");
      if (status.interests_count !== undefined && (!Number.isSafeInteger(status.interests_count) || status.interests_count < 0)) throw new Error("관심사 집계 형식이 올바르지 않습니다.");
      if (!latest.candidates.every(item => typeof item.issue_id === "string" && typeof item.title === "string" && typeof item.company === "string" && Object.hasOwn(topicNames, item.topic_id) && Array.isArray(item.bullets))) throw new Error("자료 목록의 형식이 올바르지 않습니다.");
      if (!latest.is_demo && (status.ai_enabled !== false || latest.candidates.some(item => item.analysis_status !== "pending_ai" || item.priority !== "unranked" || item.bullets.length !== 0))) throw new Error("원자료 수집 상태와 별도 AI 분석 결과가 혼합되어 있습니다. 생성 상태를 확인해주세요.");
      if (typeof status.schedule_enabled !== "boolean" || (status.schedule_enabled && (!["daily_1100_kst", "daily_0800_kst"].includes(status.planned_collection_frequency) || status.timezone !== "Asia/Seoul"))) throw new Error("자동 수집 일정과 상태 정보가 일치하지 않습니다.");
      state.request++;
      state.selected = null;
      state.detail = null;
      state.latest = latest;
      state.status = status;
      rebuildInterestView(); renderInterests();
      initializeReviews(reviewsResult);
      renderSummary();
      renderCandidates();
      renderWeekly();
      aiResult.then(result => {
        if (loadRequest !== state.loadRequest || !state.latest) return;
        initializeAI(result);
        if (state.detail) renderDetail(state.detail);
      });
      articleResult.then(result => {
        if (loadRequest !== state.loadRequest || !state.latest) return;
        initializeArticles(result);
        if (state.detail) renderDetail(state.detail);
      });
      interestsResult.then(result => {
        if (loadRequest !== state.loadRequest || !state.latest) return;
        initializeInterests(result); renderInterests();
        if (state.latest.candidates.length) renderCandidates();
      });
      if (!latest.candidates.length) {
        if (latest.is_demo) {
          $("candidate-list").replaceChildren(emptyList("데모 후보가 생성되지 않았습니다", "GitHub Actions의 실행 결과를 확인해주세요."));
          showError("데모에 필요한 후보가 없습니다. 정상적인 ‘보고할 이슈 없음’ 상태가 아닙니다.");
        } else {
          if (status.interests_only === true && status.interests_count === 0) {
            $("candidate-list").replaceChildren(emptyList("활성 관심사 없어 수집 생략", "관심사를 등록한 뒤 ‘관심사 지금 수집’에서 GitHub 요청을 제출할 수 있습니다."));
            $("detail-panel").replaceChildren(emptyList("관심사 등록을 기다리고 있습니다", "이번 별도 요청에서는 기사와 DART를 수집하지 않았습니다. 다음 정기 수집과는 별개입니다."));
            return;
          }
          const unavailable = status.publication_status === "unavailable";
          const filtered = status.publication_status === "filtered";
          $("candidate-list").replaceChildren(emptyList(filtered ? "변경된 수집 범위에 맞는 기존 자료가 없습니다" : unavailable ? "게시 가능한 수집 결과가 없습니다" : "수집 조건을 통과한 자료가 없습니다", filtered ? "이번 수집은 실패했습니다. 관리페이지에서 출처별 수집 상태와 실행 기록을 확인해주세요." : unavailable ? "관리페이지에서 출처별 수집 상태와 실행 기록을 확인해주세요." : "현재 피드 범위와 회사·주제 조건에 해당하는 자료가 없습니다. 관리페이지에서 출처별 상태를 확인해주세요."));
          $("detail-panel").replaceChildren(emptyList("분석 결과가 아닙니다", "빈 수집 목록은 ‘보고할 이슈 없음’을 의미하지 않습니다. 관리페이지에서 출처별 수집 상태를 확인해주세요."));
        }
      }
    } catch (error) {
      if (loadRequest !== state.loadRequest) return;
      state.latest = null;
      state.status = null;
      reviewState.ready = false;
      reviewState.problem = "자료 갱신에 실패했습니다. 선택 이력과 주간 목록을 확인하려면 다시 불러오세요.";
      renderReviewSummary(); renderWeekly();
      aiState.data = null; aiState.loading = false; aiState.warning = "수집 자료를 불러오지 못해 AI 결과의 대상과 버전을 확인할 수 없습니다.";
      articleState.data = null; articleState.loading = false; articleState.warning = "수집 자료를 불러오지 못해 기사 본문 요약의 근거를 확인할 수 없습니다.";
      interestsState.published = null; interestsState.loading = false; interestsState.publishedWarning = "기사 자료를 불러오지 못해 게시된 관심사 수집 결과와 관련 기사의 버전을 확인할 수 없습니다. 등록 목록은 별도로 확인합니다.";
      rebuildInterestView(); renderInterests();
      $("visible-count").textContent = "불러오기 실패";
      $("candidate-list").replaceChildren(emptyList("자료를 불러오지 못했습니다", "관리페이지에서 수집·생성·배포 상태를 확인해주세요."));
      $("detail-panel").replaceChildren(emptyList("데이터 확인이 필요합니다", "자료를 다시 불러오거나 관리페이지의 실행 기록을 확인해주세요."));
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
  $("tag-filter").addEventListener("change", renderCandidates);
  $("interest-filter").addEventListener("change",() => { interestsState.filter = $("interest-filter").value; renderCandidates(); });
  $("sort-order").addEventListener("change", renderCandidates);
  $("review-filter").addEventListener("change", renderCandidates);
  $("reset-filters").addEventListener("click", () => {
    state.topic = "";
    $("search").value = "";
    $("company-filter").value = "";
    $("tag-filter").value = "";
    $("interest-filter").value = "";
    interestsState.filter = "";
    $("review-filter").value = "";
    $("sort-order").value = state.latest?.is_demo ? "priority" : "recent";
    if (state.latest) renderCandidates();
  });
  $("reload-data").addEventListener("click",() => { void loadData({forceRegistry:true}); });
  $("interests-refresh").addEventListener("click",() => { void refreshLiveInterests({force:true}); });
  ["interests-register","interests-manage"].forEach(id => $(id).addEventListener("click",() => { registryState.dirty = true; }));
  window.addEventListener("focus",refreshRegistryOnReturn);
  document.addEventListener("visibilitychange",refreshRegistryOnReturn);
  $("interests-collect").addEventListener("click",() => {
    window.open(Interests ? Interests.collectionURL() : "https://github.com/iesilder/kw-plan-weekly-trend/issues/new?template=interest-collection.yml","_blank","noopener,noreferrer");
    toast("GitHub에서 수집 요청을 제출하면 등록부를 새로 확인한 뒤 실행합니다.");
  });
  $("history-open").addEventListener("click", () => $("history-dialog").showModal());
  $("history-mobile").addEventListener("click", () => $("history-dialog").showModal());
  $("history-close").addEventListener("click", () => $("history-dialog").close());
  $("copy-close").addEventListener("click", () => $("copy-dialog").close());
  for (const dialog of [$("history-dialog"), $("copy-dialog"), $("review-save-dialog"), $("review-import-dialog")]) dialog.addEventListener("click", event => { if (event.target === dialog) { const bounds = dialog.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close(); } });
  $("review-save").addEventListener("click", openSaveDialog);
  $("review-save-close").addEventListener("click", () => $("review-save-dialog").close());
  $("review-patch-copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("review-patch").value); toast("저장 내용을 복사했습니다. GitHub의 review_patch 칸에 붙여넣으세요."); }
    catch { $("review-patch").focus(); $("review-patch").select(); toast("저장 내용을 선택했습니다. 직접 복사해주세요."); }
  });
  $("review-backup").addEventListener("click", () => {
    try {
      const draft = Review.validateDraft(reviewState.draft);
      const patch = {schema_version:"1.0",base_revision:draft.base_revision,changes:Object.entries(draft.changes).map(([issue_id,status]) => ({issue_id,status}))};
      downloadText(JSON.stringify(patch,null,2), `weekly-review-backup-v${draft.base_revision}.json`, "application/json;charset=utf-8");
      toast("임시선택 백업을 내려받았습니다. GitHub에는 아직 저장되지 않았습니다.");
    } catch(error) { toast(error.message); }
  });
  $("review-import").addEventListener("click", () => { $("review-import-text").value = ""; $("review-import-error").hidden = true; $("review-import-dialog").showModal(); });
  $("review-import-close").addEventListener("click", () => $("review-import-dialog").close());
  $("review-import-apply").addEventListener("click", importDraft);
  $("review-discard").addEventListener("click", () => {
    if (!reviewState.ready) return;
    reviewState.draft = Review.createDraft(reviewState.canonical);
    reviewState.localInvalid = false;
    saveLocalDraft(); renderReviewSummary(); renderCandidates();
    if (state.detail) renderDetail(state.detail);
    renderWeekly(); toast("브라우저 임시선택을 버리고 GitHub에 저장된 선택을 표시합니다.");
  });
  ["week-start","week-end"].forEach(id => $(id).addEventListener("change", renderWeekly));
  $("week-current").addEventListener("click", () => { setWeekRange(Review.weekRange()); renderWeekly(); });
  for (const [id, offset] of [["week-prev",-7],["week-next",7]]) $(id).addEventListener("click", () => {
    try { const date = new Date(`${selectedRange().start}T00:00:00Z`); date.setUTCDate(date.getUTCDate()+offset); setWeekRange(Review.weekRange(date.toISOString().slice(0,10))); renderWeekly(); }
    catch(error) { toast(error.message); }
  });
  $("weekly-copy").addEventListener("click", () => { if (reviewState.weeklyRecords.length && !draftProblem()) copyText(Review.buildText(reviewState.weeklyRecords,reviewState.weeklyRange), "주간 보고 후보 목록", "채택한 자료의 제목·출처·URL·날짜이며 실제 보고 완료 이력에 저장되지 않습니다."); });
  $("weekly-csv").addEventListener("click", () => {
    if (!reviewState.weeklyRecords.length || draftProblem()) return;
    const {start,end} = reviewState.weeklyRange;
    downloadText(Review.buildCSV(reviewState.weeklyRecords,reviewState.weeklyRange), `weekly-candidates-${start}-${end}.csv`, "text/csv;charset=utf-8");
    toast("주간 후보 CSV를 내려받았습니다. 실제 보고 완료로 기록되지 않습니다.");
  });
  loadData();
})();
