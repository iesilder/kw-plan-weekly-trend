"use strict";

// Independent of the article snapshot: failed builds and deploys must stay visible.
(() => {
  const Runs = window.WeeklyBatchRuns;
  const $ = id => document.getElementById(id);
  const panel = $("batch-runs");
  if (!panel) return;
  const labels = {waiting:"예정 · 대기",no_record:"실행 기록 없음",unknown:"확인 불가",queued:"실행 대기",running:"실행 중",success:"성공",failure:"실패",cancelled:"취소",skipped:"건너뜀"};
  const client = Runs?.createClient({fetchImpl:(...args) => fetch(...args)});
  let model = null;
  let pending = false;
  let lastAttempt = 0;

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text != null) element.textContent = String(text);
    return element;
  }

  function stamp(value) {
    if (!value || Number.isNaN(new Date(value).getTime())) return "—";
    const parts = new Intl.DateTimeFormat("ko-KR",{timeZone:"Asia/Seoul",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hourCycle:"h23"}).formatToParts(new Date(value));
    const p = Object.fromEntries(parts.map(part => [part.type,part.value]));
    return `${p.month}.${p.day} ${p.hour}:${p.minute}:${p.second}`;
  }

  function setStatus(id, state, text) {
    $(id).textContent = text || labels[state] || labels.unknown;
    $(id).className = `batch-status batch-status-${Object.hasOwn(labels,state) ? state : "unknown"}`;
  }

  function render() {
    const today = Runs ? Runs.deriveToday(model) : {state:"unknown",record:null};
    $("batch-schedule-label").textContent = `오늘 정기 배치${today.dateKst ? ` · ${today.dateKst}` : ""} · 11:00 한국 시간`;
    setStatus("batch-today-state",today.state);
    const todayNotes = {
      waiting:"예약 시각 전입니다. 수동 수집은 정기 배치와 별도로 기록합니다.",
      no_record:"예정 시각 이후 확인한 예약 실행 기록이 없습니다. GitHub의 실행 지연 가능성이 있으며 실패로 확정한 상태는 아닙니다.",
      unknown:"오늘의 예약 실행 여부를 확인할 수 없습니다. 마지막 확인 시각과 전체 실행 로그를 확인하세요."
    };
    $("batch-today-note").textContent = todayNotes[today.state] || `${today.record?.label || "정기 자동 실행"} · 시작 ${stamp(today.record?.startedAt)} · 최종 갱신 ${stamp(today.record?.updatedAt)}`;
    const latest = model?.records?.[0];
    setStatus("batch-latest-state",latest?.state || "unknown",latest ? `${latest.label} · ${labels[latest.state] || labels.unknown}` : model && !model.warning ? "최근 30일 실행 기록 없음" : "확인 불가");
    $("batch-latest-note").textContent = latest ? `시작 ${stamp(latest.startedAt)} · 실행 #${latest.id} · 시도 ${latest.attempt}` : "관심사 수동 실행을 정기 자동 실행으로 표시하지 않습니다.";
    const workflowChecks = model ? Object.values(model.workflows).filter(item => item.checkedAt).map(item => new Date(item.checkedAt).getTime()) : [];
    const oldestCheck = workflowChecks.length ? new Date(Math.min(...workflowChecks)).toISOString() : null;
    $("batch-checked").textContent = pending ? "GitHub 실행 기록 확인 중…" : oldestCheck ? `실행 기록 확인 ${stamp(oldestCheck)} KST${model.cached ? " · 최근 조회 기록" : ""}` : "GitHub 실행 기록 확인 불가";
    $("batch-warning").textContent = model?.warning || (!Runs ? "실행 기록 기능을 불러오지 못했습니다. 전체 실행 로그에서 확인하세요." : "");
    $("batch-warning").hidden = !$("batch-warning").textContent;
    const body = $("batch-log-body");
    body.replaceChildren();
    const records = model?.records || [];
    for (const record of records.slice(0,20)) {
      const row = node("tr");
      const kind = node("td");
      kind.append(node("strong","batch-kind",record.label),node("small","batch-run-id",`#${record.id} · 시도 ${record.attempt}`));
      const start = node("td","batch-time",stamp(record.startedAt));
      if (!record.startedAt) start.append(node("small","batch-run-id",`요청 ${stamp(record.createdAt)}`));
      const result = node("td");
      result.append(node("span",`batch-status batch-status-${record.state}`,labels[record.state] || labels.unknown));
      const detail = node("td");
      const link = node("a","batch-log-link","로그 ↗");
      link.href = record.url; link.target = "_blank"; link.rel = "noopener noreferrer";
      link.setAttribute("aria-label",`${record.label} #${record.id} 시도 ${record.attempt} 상세 로그`);
      detail.append(link);
      row.append(kind,start,node("td","batch-time",stamp(record.updatedAt)),result,detail);
      body.append(row);
    }
    if (!records.length) {
      const cell = node("td","batch-empty",pending ? "실행 이력을 불러오는 중입니다." : model?.warning ? "실행 이력 확인 불가 · 전체 실행 로그를 확인하세요." : "조회한 최근 30일에 수집 실행 기록이 없습니다.");
      cell.colSpan = 5;
      const row = node("tr"); row.append(cell); body.append(row);
    }
    $("batch-log-count").textContent = records.length > 20 ? `최근 20건 표시 / 조회 ${records.length}건` : `${records.length}건`;
    $("batch-refresh").disabled = pending;
    $("batch-refresh").setAttribute("aria-busy",String(pending));
    $("batch-download").disabled = !model;
  }

  async function refresh(force = false) {
    if (!client || pending) { if (!client) render(); return; }
    pending = true; lastAttempt = Date.now(); render();
    try { model = await client.load({force}); }
    catch {
      // Never let an old success stand in for a failed live lookup.
      model = {...model,records:model?.records || [],workflows:{},checkedAt:null,warning:"GitHub 실행 기록을 확인하지 못했습니다. 전체 실행 로그에서 확인하세요."};
    } finally { pending = false; render(); }
  }

  $("batch-refresh").addEventListener("click",() => { void refresh(true); });
  $("batch-download").addEventListener("click",() => {
    if (!model) return;
    const today = Runs.deriveToday(model);
    const json = JSON.stringify({schema_version:"1.0",source:"GitHub Actions",repository:"iesilder/kw-plan-weekly-trend",timezone:"Asia/Seoul",exported_at:new Date().toISOString(),today,...model},null,2);
    const url = URL.createObjectURL(new Blob([json],{type:"application/json;charset=utf-8"}));
    const link = node("a"); link.href = url; link.download = `batch-runs-${today.dateKst}.json`;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url),1000);
  });
  function onReturn() {
    if (document.visibilityState === "hidden") return;
    if (Date.now() - lastAttempt >= 5 * 60 * 1000) void refresh();
    else render();
  }
  window.addEventListener("focus",onReturn);
  document.addEventListener("visibilitychange",onReturn);
  // Re-evaluate the Korean date/11:00 boundary without polling GitHub in a loop.
  setInterval(() => { if (document.visibilityState !== "hidden") render(); },60000);
  void refresh();
})();
