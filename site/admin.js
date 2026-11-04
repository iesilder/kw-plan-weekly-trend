"use strict";

// Read-only operational view. Article review and human choices belong to the main page.
(() => {
  const $ = id => document.getElementById(id);
  const reload = $("admin-reload");
  if (!reload) return;
  const AI = window.WeeklyAI, Article = window.WeeklyArticle;
  const state = {request:0,latest:null,status:null};
  const paths = ["data/latest.json","data/status.json","data/ai-analysis.json","data/article-analysis.json"];
  const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const integer = value => Number.isSafeInteger(value) && value >= 0;
  const string = (value,length) => typeof value === "string" && value.length <= length;
  const set = (id,text) => { const node = $(id); if (node) node.textContent = text; };
  const instant = value => {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) return false;
    const day = value.slice(0,10), date = new Date(`${day}T00:00:00Z`);
    return Number.isFinite(date.getTime()) && date.toISOString().slice(0,10) === day && +value.slice(11,13) < 24 && +value.slice(14,16) < 60 && +value.slice(17,19) < 60;
  };

  function el(tag,className,text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function formatDate(value,includeTime = true) {
    if (!value || !Number.isFinite(new Date(value).getTime())) return "날짜 미확인";
    const parts = new Intl.DateTimeFormat("ko-KR",{timeZone:"Asia/Seoul",year:"numeric",month:"2-digit",day:"2-digit",...(includeTime ? {hour:"2-digit",minute:"2-digit",hourCycle:"h23"} : {})}).formatToParts(new Date(value));
    const p = Object.fromEntries(parts.map(part => [part.type,part.value]));
    return `${p.year}.${p.month}.${p.day}${includeTime ? ` · ${p.hour}:${p.minute}` : ""}`;
  }
  function metric(id,value,unit) {
    $(id).replaceChildren(document.createTextNode(String(value)),el("small","",unit));
  }
  function warning(id,message) {
    set(id,message); $(id).hidden = !message;
  }
  async function getJSON(path) {
    const controller = new AbortController();
    let timer;
    const timeout = new Promise((_,reject) => { timer = setTimeout(() => { controller.abort(); reject(new Error("timeout")); },15000); });
    const work = (async () => {
      const response = await fetch(path,{method:"GET",credentials:"omit",cache:"no-store",referrerPolicy:"no-referrer",signal:controller.signal});
      if (!response || !response.ok) throw new Error("network");
      const body = await response.text();
      if (typeof body !== "string" || new TextEncoder().encode(body).byteLength > 6000000) throw new Error("size");
      return JSON.parse(body);
    })();
    try { return await Promise.race([work,timeout]); }
    finally { clearTimeout(timer); }
  }
  function validatePrimary(latest,status) {
    if (!object(latest) || latest.schema_version !== "1.0" || typeof latest.is_demo !== "boolean" || !string(latest.generation_id,200) || !latest.generation_id || !instant(latest.generated_at) || !Array.isArray(latest.candidates) || latest.candidates.length > 10000 || !object(status) || status.schema_version !== "1.0" || status.is_demo !== latest.is_demo || (status.published_generation_id || status.generation_id) !== latest.generation_id) throw new Error("snapshot");
    if (!["article_count","version_count","run_count"].every(key => integer(status[key])) || (status.issue_count !== undefined && (!integer(status.issue_count) || status.issue_count !== latest.candidates.length)) || (status.interests_count !== undefined && !integer(status.interests_count))) throw new Error("counts");
    if (new Set(latest.candidates.map(item => item?.issue_id)).size !== latest.candidates.length || !latest.candidates.every(item => object(item) && /^issue-[a-f0-9]{20}$/.test(item.issue_id) && string(item.title,1000) && Array.isArray(item.evidence_ids))) throw new Error("candidates");
    if (typeof status.schedule_enabled !== "boolean" || (status.schedule_enabled && (!["daily_1100_kst","daily_0800_kst"].includes(status.planned_collection_frequency) || status.timezone !== "Asia/Seoul"))) throw new Error("schedule");
    for (const key of ["last_run_at","last_successful_run_at"]) if (status[key] != null && !instant(status[key])) throw new Error("date");
    if (!latest.is_demo) {
      if (!["updated","retained","filtered","unavailable"].includes(status.publication_status) || !Array.isArray(status.sources) || status.sources.length > 100) throw new Error("sources");
      for (const source of status.sources) {
        if (!object(source) || !string(source.source_name || source.source_id,300) || !["success","not_modified","failed"].includes(source.status)) throw new Error("source");
        for (const key of ["fetched_count","accepted_count","inserted_versions","duplicate_count","rejected_count","outside_scope_count","outside_window_count","outside_content_filter_count"]) if (source[key] != null && !integer(source[key])) throw new Error("source_count");
        for (const key of ["last_attempt_at","last_success_at","coverage_start","coverage_end"]) if (source[key] != null && !instant(source[key])) throw new Error("source_date");
        if (source.coverage_note != null && !string(source.coverage_note,2000)) throw new Error("source_note");
      }
    }
  }
  function clearPanels(loading) {
    state.latest = null; state.status = null;
    warning("admin-error","");
    for (const [id,unit] of [["issue-count","건"],["article-count","건"],["version-count","개"]]) metric(id,"—",unit);
    set("last-run",loading ? "확인 중" : "확인 불가"); set("run-count","—회");
    set("mode-badge","관리"); set("phase-label","MANAGEMENT");
    set("notice-title",loading ? "저장된 처리 현황을 확인하고 있습니다" : "저장 자료 확인 불가");
    set("notice-description",loading ? "화면 조회는 수집이나 AI 분석을 실행하지 않습니다." : "배치 실행 기록은 위 영역에서 독립적으로 확인할 수 있습니다.");
    $("source-status-panel").hidden = false;
    set("source-status-summary",loading ? "확인 중" : "확인 불가");
    set("source-status-note",loading ? "출처별 저장 상태를 확인합니다." : "유효한 저장 자료를 확인하지 못해 출처별 건수를 표시하지 않습니다.");
    $("source-status-list").replaceChildren();
    set("execution-mode",loading ? "처리 현황 확인 중" : "처리 현황 확인 필요");
    set("connection-status",loading ? "저장된 수집·분석 현황" : "배치 실행 로그에서 별도 확인");
    $("ai-counts").replaceChildren(); $("article-counts").replaceChildren();
    set("ai-readiness",loading ? "확인 중" : "상태 확인 불가");
    set("article-readiness",loading ? "확인 중" : "상태 확인 불가");
    set("ai-summary-note",loading ? "저장된 제목 분석 상태를 확인하고 있습니다. 이 화면에서 AI를 호출하지 않습니다." : "유효한 현재 자료를 확인하지 못해 AI 처리 건수를 표시하지 않습니다.");
    set("ai-last-run",loading ? "실제 분석 이력 확인 중" : "실제 분석 이력: 확인할 수 없음");
    set("article-last-run",loading ? "저장된 본문 분석 상태를 확인하고 있습니다." : "실제 본문 분석 이력: 확인할 수 없음");
    warning("ai-warning",""); warning("article-warning","");
    set("admin-loaded-at",loading ? "저장 자료 확인 중" : "저장 자료 확인 불가");
  }
  function renderCollection() {
    const {latest,status} = state;
    const demo = latest.is_demo, interestOnly = status.interests_only === true, interestSkipped = interestOnly && status.interests_count === 0;
    set("issue-count-label",demo ? "검토 후보" : "수집 자료");
    set("article-count-label",demo ? "원자료" : "현재 범위 자료");
    set("version-count-label",demo ? "보존한 자료 버전" : "현재 범위 자료 버전");
    set("last-run-label",demo ? "마지막 데모 실행" : interestOnly ? "마지막 관심사 별도 수집" : "마지막 수집 시도");
    metric("issue-count",latest.candidates.length,"건"); metric("article-count",status.article_count,"건"); metric("version-count",status.version_count,"개");
    set("last-run",formatDate(status.last_run_at || latest.generated_at)); set("run-count",`${status.run_count}회`);
    set("mode-badge",demo ? "DEMO" : "관리");
    set("notice-title",demo ? "가상 데이터 · 실제 기사/AI 분석 아님" : interestOnly ? "마지막 실행: 관심사 별도 수집" : "저장된 수집·분석 현황");
    set("notice-description",demo ? "가상 자료의 처리 현황이며 실제 수집·분석 이력이 아닙니다." : interestOnly ? `${interestSkipped ? "활성 관심사가 없어 기사 수집을 생략했습니다." : "관심사에 대한 RSS 기사·보도자료만 별도로 수집을 시도한 실행입니다."} DART 공시는 이번 요청에서 갱신하지 않았습니다. 기존 자료의 발행·수집 시각과 실행 결과를 확인하세요.` : status.schedule_enabled ? "매일 11:00(한국 시간) 자료 수집·기사 분석을 예약했습니다. 아래는 마지막 저장 결과이며 실제 배치 실행 여부는 위 실행 기록에서 확인하세요." : "수동으로 저장한 수집 현황입니다. 실제 실행 결과와 출처별 상태를 함께 확인하세요.");
    set("execution-mode",status.schedule_enabled ? "매일 11:00 자동 실행 예약" : "수동 실행 모드");
    set("connection-status",`${status.schedule_enabled ? "매일 11:00 KST 예약" : "수동 수집"} · 분석 상태 확인 중`);
    renderSources();
  }
  function renderSources() {
    const {latest,status} = state, panel = $("source-status-panel"), list = $("source-status-list");
    list.replaceChildren(); panel.hidden = latest.is_demo;
    if (panel.hidden) return;
    const sources = status.sources, interestOnly = status.interests_only === true, interestSkipped = interestOnly && status.interests_count === 0;
    const counts = Object.fromEntries(["success","not_modified","failed"].map(key => [key,sources.filter(source => source.status === key).length]));
    const publication = status.publication_status;
    set("source-status-note",interestOnly ? "마지막 관심사 별도 수집의 RSS 출처 상태입니다. DART는 이번 요청에 포함되지 않으며 기존 자료의 수집 시각을 확인해야 합니다." : "각 출처의 제공 범위 안에서만 수집합니다. 실패·미분류·필터 제외 내역을 확인하세요.");
    set("source-status-summary",interestSkipped ? "활성 관심사 없어 수집 생략" : `${interestOnly ? "관심사 RSS · " : ""}성공 ${counts.success} · 304 ${counts.not_modified} · 실패 ${counts.failed}${publication === "filtered" ? " · 기존 자료에 변경된 수집 범위 적용" : publication === "retained" ? " · 이전 자료 유지" : publication === "unavailable" ? " · 게시 자료 없음" : ""}`);
    panel.open = counts.failed > 0 || ["filtered","unavailable"].includes(publication);
    if (publication === "filtered") list.append(el("p","source-alert",`수집 실패 · 기존 자료에 변경된 수집 범위 적용. 이번 수집은 성공하지 않았으며 기존에 보관한 자료를 표시합니다. 최근 성공 실행: ${formatDate(status.last_successful_run_at)}.`));
    if (publication === "retained") list.append(el("p","source-alert",`현재 화면은 마지막으로 게시한 자료를 유지합니다. 자료 생성: ${formatDate(latest.generated_at)}. 최근 성공 실행: ${formatDate(status.last_successful_run_at)}.`));
    if (publication === "unavailable") list.append(el("p","source-alert","아직 게시할 수 있는 수집 결과가 없습니다. 이 상태는 ‘보고할 이슈 없음’을 뜻하지 않습니다."));
    if (integer(status.archived_article_count) && status.archived_article_count > 0) list.append(el("p","source-coverage",`현재 범위 자료 ${status.article_count}건을 표시합니다. 범위에서 제외된 보관 자료 ${status.archived_article_count}건은 현재 집계에 포함하지 않습니다.`));
    if (interestSkipped) list.append(el("p","source-coverage","활성 관심사가 없어 수집을 생략하고 등록부와 화면 정보를 갱신했습니다. 메인에서 관심사를 등록한 뒤 별도 수집을 요청할 수 있습니다."));
    else if (!sources.length) list.append(el("p","source-alert","출처별 수집 상태가 제공되지 않았습니다. 배치 실행 기록을 확인하세요."));
    const labels = {success:"수집 성공",not_modified:"변경 없음 · 304",failed:"수집 실패"};
    for (const source of sources) {
      const row = el("article","source-status-item"), heading = el("div","source-status-heading");
      heading.append(el("h3","",source.source_name || source.source_id),el("span",`source-state${source.status === "failed" ? " failed" : ""}`,labels[source.status]));
      row.append(heading,el("p","source-status-meta",`최근 시도 ${formatDate(source.last_attempt_at)} · 최근 성공 ${source.last_success_at ? formatDate(source.last_success_at) : "성공 이력 없음"}`));
      const excluded = source.outside_scope_count == null && source.outside_content_filter_count == null ? "—" : (source.outside_scope_count || 0) + (source.outside_content_filter_count || 0);
      row.append(el("p","source-status-meta",`피드 항목 ${source.fetched_count ?? "—"}건 · 조건 통과 ${source.accepted_count ?? "—"}건 · 조건 제외 ${excluded}건 · 기간 제외 ${source.outside_window_count ?? "—"}건 · 유효성 제외 ${source.rejected_count ?? "—"}건 · 중복 ${source.duplicate_count ?? "—"}건 · 새 버전 ${source.inserted_versions ?? "—"}개`));
      if (source.status === "failed") row.append(el("p","source-error","출처 요청에 실패했습니다. 상세 내용은 해당 배치 실행 로그에서 확인하세요."));
      if (source.status === "not_modified") row.append(el("p","source-coverage","HTTP 304: 출처가 피드 변경 없음으로 응답해 기존 저장 자료를 유지합니다."));
      const range = source.coverage_start || source.coverage_end ? `조회 날짜 범위 ${formatDate(source.coverage_start,false)} ~ ${formatDate(source.coverage_end,false)}. ` : "조회한 날짜 범위 없음. ";
      row.append(el("p","source-coverage",range + (source.coverage_note || "피드에서 확인 가능한 항목에 한하며 전체 기간 수집을 보장하지 않습니다."))); list.append(row);
    }
  }
  function renderAI(result) {
    try {
      if (state.latest.is_demo || result.status !== "fulfilled" || !AI) throw new Error("unavailable");
      const data = AI.validateSidecar(result.value,state.latest), summary = AI.summary(data);
      set("ai-readiness",summary.label);
      set("ai-summary-note","기사·보도자료 제목의 관련성을 분석합니다. DART 공시는 AI에 전송하지 않습니다. 본문 개조식 요약은 아래 별도 현황에서 확인하며 담당자의 선택은 메인에서 관리합니다.");
      for (const [label,value] of [["분석 가능",data.counts.eligible],["현재 결과",data.counts.completed],["재분석 필요",data.counts.stale],["아직 미분석",data.counts.not_analyzed],["입력 제외",data.counts.blocked],["분석 실패",data.counts.failed],["키·설정 확인 필요",summary.keyRequiredCount === null ? "미확인" : summary.keyRequiredCount]]) {
        const count = el("div","ai-count"); count.append(el("span","",label),el("strong","",typeof value === "number" ? `${value}건` : value)); $("ai-counts").append(count);
      }
      set("ai-last-run",summary.lastAnalysisAt ? `최근 실제 분석 ${formatDate(summary.lastAnalysisAt)} · 한국 시간 기준` : "아직 실제 AI 분석 이력이 없습니다. 준비 상태를 분석 완료로 집계하지 않습니다.");
      set("connection-status",`${state.status.schedule_enabled ? "매일 11:00 KST 예약" : "수동 수집"} · ${data.auto_analysis_enabled ? "기사 AI 자동 분석 설정" : "AI 수동 보조"}`);
      const notes = [];
      if (!data.last_attempt) notes.push("첫 분석 실행 전에는 API 키와 무료 프로젝트 설정 여부를 확인하지 않습니다.");
      if (summary.keyRequiredCount > 0) notes.push("GitHub의 키·무료 프로젝트 설정을 확인하세요. 이 사이트에는 비밀키를 입력하지 않습니다.");
      if (data.counts.blocked) notes.push("DART 공시는 AI 입력에서 제외하며 목록과 원문 링크만 유지합니다.");
      if (data.last_attempt && ["failed","partial_success"].includes(data.last_attempt.status)) notes.push("최근 실행에 실패 항목이 있습니다. 배치 실행 기록을 확인하세요.");
      warning("ai-warning",notes.join(" "));
    } catch {
      set("ai-readiness","상태 확인 불가"); set("ai-summary-note","현재 수집 자료와 일치하는 제목 분석 결과를 확인하지 못했습니다.");
      set("ai-last-run","실제 분석 이력: 확인할 수 없음");
      warning("ai-warning",state.latest.is_demo ? "가상 데모에는 실제 AI 분석 결과를 연결하지 않습니다." : "AI 보조 자료를 불러오지 못했거나 현재 자료와 버전·근거·건수가 일치하지 않습니다. 다시 불러오거나 배치 실행 로그를 확인하세요.");
      set("connection-status",`${state.status.schedule_enabled ? "매일 11:00 KST 예약" : "수동 수집"} · AI 상태 확인 필요`);
    }
  }
  function renderArticle(result) {
    try {
      if (state.latest.is_demo || result.status !== "fulfilled" || !Article) throw new Error("unavailable");
      const data = Article.validateSidecar(result.value,state.latest), summary = Article.summary(data);
      set("article-readiness",summary.label);
      for (const [label,key] of [["기사 대상","eligible"],["현재 요약","completed"],["본문 확보 실패","unavailable"],["미분석","pending"],["재분석 필요","stale"],["분석 실패","failed"]]) {
        const count = el("div","ai-count"); count.append(el("span","",label),el("strong","",`${data.counts[key]}건`)); $("article-counts").append(count);
      }
      set("article-last-run",summary.lastAnalysisAt ? `최근 본문 분석 ${formatDate(summary.lastAnalysisAt)} · 한국 시간 기준` : "아직 현재 자료에 대한 본문 분석 완료 이력이 없습니다.");
      warning("article-warning",data.last_attempt?.error_code || data.counts.unavailable || data.counts.failed ? "본문 확보 또는 분석이 끝나지 않은 자료가 있습니다. 메인 자료 상세와 배치 실행 로그에서 사유를 확인하세요." : "");
    } catch {
      set("article-readiness","상태 확인 불가"); set("article-last-run","실제 본문 분석 이력: 확인할 수 없음");
      warning("article-warning",state.latest.is_demo ? "가상 데모에는 실제 기사 본문 요약을 연결하지 않습니다." : "기사 요약 자료를 불러오지 못했거나 현재 자료와 버전·근거·건수가 일치하지 않습니다. 제목 분석 현황은 별도로 확인할 수 있습니다.");
    }
  }
  async function load() {
    const request = ++state.request;
    clearPanels(true); reload.disabled = true; reload.setAttribute("aria-busy","true");
    try {
      const result = await Promise.allSettled(paths.map(getJSON));
      if (request !== state.request) return;
      if (result[0].status !== "fulfilled" || result[1].status !== "fulfilled") throw new Error("primary");
      validatePrimary(result[0].value,result[1].value);
      state.latest = result[0].value; state.status = result[1].value;
      renderCollection(); renderAI(result[2]); renderArticle(result[3]);
      set("admin-loaded-at",`저장 자료 확인 ${formatDate(new Date().toISOString())} · 한국 시간 기준`);
    } catch {
      if (request !== state.request) return;
      clearPanels(false);
      warning("admin-error","현재 수집 자료와 저장 상태를 불러오지 못했거나 생성 버전·집계가 일치하지 않습니다. 다시 불러오세요. 배치 실행 기록은 독립적으로 확인할 수 있습니다.");
      warning("ai-warning","현재 자료의 생성 버전을 확인할 수 없어 제목 분석 건수를 표시하지 않습니다.");
      warning("article-warning","현재 자료의 생성 버전을 확인할 수 없어 본문 요약 건수를 표시하지 않습니다.");
    } finally {
      if (request === state.request) { reload.disabled = false; reload.setAttribute("aria-busy","false"); }
    }
  }
  reload.addEventListener("click",() => { void load(); });
  $("history-open")?.addEventListener("click",() => $("history-dialog")?.showModal());
  $("history-close")?.addEventListener("click",() => $("history-dialog")?.close());
  $("history-dialog")?.addEventListener("click",event => {
    if (event.target !== $("history-dialog")) return;
    const bounds = event.target.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) event.target.close();
  });
  void load();
})();
