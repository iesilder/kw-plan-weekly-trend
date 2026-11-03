/* Validate the manual AI sidecar independently from source records and human choices. */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.WeeklyAI = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";
  const TOPICS = new Set(["performance", "digital", "expansion", "strategy", "risk"]);
  const STATUSES = new Set(["blocked", "not_analyzed", "completed", "stale", "failed"]);
  const RELEVANCE = Object.freeze({relevant:"검토 대상에 해당", uncertain:"판단 유보 · 원문 확인", out_of_scope:"검토 범위 밖으로 제안"});
  const STATUS_LABELS = Object.freeze({blocked:"AI 이용 제한", not_analyzed:"아직 분석하지 않음", completed:"AI 제안 · 담당자 확인 필요", stale:"자료 변경 · 재분석 필요", failed:"분석 실패"});
  const REASONS = Object.freeze({source_ai_prohibited:"이 출처는 AI 이용이 금지되어 분석 대상으로 보내지 않습니다.", rights_unconfirmed:"AI 이용 범위가 확인되지 않은 출처여서 분석을 보류합니다.", source_not_eligible:"현재 승인된 AI 입력 범위에 해당하지 않습니다.", not_live_metadata:"실제 공개 메타데이터가 아니므로 분석하지 않습니다.", invalid_factual_metadata:"허용된 사실 메타데이터를 확인하지 못해 분석하지 않습니다."});
  const KEY_CODES = new Set(["missing_api_key", "free_project_not_confirmed"]);
  const HASH = /^[a-f0-9]{64}$/;
  const ID = /^issue-[a-f0-9]{20}$/;
  const VERSION = /^version-[a-f0-9]{20}$/;
  const plainObject = value => value !== null && typeof value === "object" && !Array.isArray(value);
  function fail(text) { throw new Error(text); }
  function string(value, max = 2000) { return typeof value === "string" && value.length <= max; }
  function integer(value) { return Number.isSafeInteger(value) && value >= 0; }
  function instant(value) {
    if (typeof value !== "string") return false;
    const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$/.exec(value);
    if (!match || +match[2] > 23 || +match[3] > 59 || +match[4] > 59) return false;
    const day = new Date(`${match[1]}T00:00:00Z`);
    if (Number.isNaN(day.getTime()) || day.toISOString().slice(0,10) !== match[1]) return false;
    if (match[5] !== "Z" && (+match[5].slice(1,3) > 23 || +match[5].slice(4) > 59)) return false;
    return !Number.isNaN(new Date(value).getTime());
  }
  function array(value, check, max = 1000) { return Array.isArray(value) && value.length <= max && value.every(check); }
  function sameIds(a,b) { return [...a].sort().join("|") === [...b].sort().join("|"); }
  function allowedSource(item) { return item.is_demo === false && item.content_filter === "earnings_only" && typeof item.source_name === "string" && item.source_name.startsWith("DART · "); }
  function validateResult(result, issueId, allIds) {
    if (!plainObject(result) || !TOPICS.has(result.suggested_topic_id) || !Object.hasOwn(RELEVANCE,result.relevance) || !string(result.rationale,300) || !result.rationale.trim() || !array(result.check_points,text => string(text,150) && !!text.trim(),3) || !array(result.duplicate_issue_ids,id => ID.test(id) && id !== issueId && (!allIds || allIds.has(id)),4) || new Set(result.duplicate_issue_ids).size !== result.duplicate_issue_ids.length) fail("AI 제안의 필수 항목이나 참조 자료를 확인할 수 없습니다.");
    return {suggested_topic_id:result.suggested_topic_id,relevance:result.relevance,rationale:result.rationale,check_points:[...result.check_points],duplicate_issue_ids:[...result.duplicate_issue_ids]};
  }
  function validateSidecar(value, latest) {
    if (!plainObject(value) || value.schema_version !== "1.0" || !latest || latest.is_demo !== false || !Array.isArray(latest.candidates)) fail("AI 보조 자료의 형식을 확인할 수 없습니다.");
    if (value.generation_id !== latest.generation_id) fail("AI 보조 자료와 현재 수집 자료의 버전이 다릅니다. 최신 자료에 대한 결과로 표시하지 않습니다.");
    if (!instant(value.generated_at) || value.provider !== "google_gemini" || !string(value.model,100) || !value.model || !string(value.prompt_version,100) || !value.prompt_version || !string(value.policy_version,100) || !value.policy_version || value.auto_analysis_enabled !== false || !plainObject(value.items) || !plainObject(value.counts)) fail("AI 보조 자료의 실행 설정이나 생성 정보를 확인할 수 없습니다.");
    const candidates = new Map(latest.candidates.map(item => [item.issue_id,item]));
    const allIds = new Set(candidates.keys());
    const eligibleIds = new Set(latest.candidates.filter(item => allowedSource(item) && value.items[item.issue_id]?.status !== "blocked").map(item => item.issue_id));
    if (allIds.size !== latest.candidates.length || !sameIds(Object.keys(value.items),[...allIds])) fail("AI 보조 자료의 대상 목록이 현재 수집 자료와 다릅니다.");
    const result = {schema_version:"1.0",generation_id:value.generation_id,generated_at:value.generated_at,provider:value.provider,model:value.model,prompt_version:value.prompt_version,policy_version:value.policy_version,auto_analysis_enabled:false,last_attempt:null,counts:{eligible:0,blocked:0,completed:0,stale:0,failed:0,not_analyzed:0},items:{}};
    for (const [issueId, item] of Object.entries(value.items)) {
      if (!ID.test(issueId) || !plainObject(item) || !STATUSES.has(item.status) || !(item.reason_code === null || Object.hasOwn(REASONS,item.reason_code)) || !(item.error_code === null || string(item.error_code,100))) fail("개별 AI 처리 상태의 형식이 올바르지 않습니다.");
      const candidate = candidates.get(issueId);
      if (!array(item.input_evidence_ids,id => typeof id === "string" && VERSION.test(id)) || new Set(item.input_evidence_ids).size !== item.input_evidence_ids.length) fail("AI 입력 근거의 형식이 올바르지 않습니다.");
      if (item.status === "blocked") {
        if (!item.reason_code || item.input_evidence_ids.length || item.input_fingerprint !== null || item.result_fingerprint !== null || item.analyzed_at !== null || item.result !== null) fail("이용 제한 자료에 분석 결과가 포함되어 있습니다.");
        result.counts.blocked++;
      } else {
        if (!allowedSource(candidate) || item.reason_code !== null || !HASH.test(item.input_fingerprint || "") || !array(candidate.evidence_ids,id => typeof id === "string") || !sameIds(item.input_evidence_ids,candidate.evidence_ids)) fail("AI 입력 범위나 현재 자료의 근거 버전이 일치하지 않습니다.");
        result.counts.eligible++;
        result.counts[item.status]++;
        if (item.status === "completed" || item.status === "stale") {
          if (!instant(item.analyzed_at) || !HASH.test(item.result_fingerprint || "") || (item.status === "completed" && item.result_fingerprint !== item.input_fingerprint)) fail("AI 결과의 분석 시각이나 입력 버전을 확인할 수 없습니다.");
          validateResult(item.result,issueId,item.status === "stale" ? null : eligibleIds);
        } else if (item.result !== null || item.analyzed_at !== null || item.result_fingerprint !== null) fail("분석되지 않은 자료에 결과가 포함되어 있습니다.");
      }
      result.items[issueId] = {status:item.status,reason_code:item.reason_code,input_fingerprint:item.input_fingerprint,input_evidence_ids:[...item.input_evidence_ids],result_fingerprint:item.result_fingerprint,analyzed_at:item.analyzed_at,error_code:item.error_code,result:item.result === null ? null : validateResult(item.result,issueId,item.status === "stale" ? null : eligibleIds)};
    }
    for (const [key,count] of Object.entries(result.counts)) if (value.counts[key] !== count) fail("AI 처리 건수가 자료별 상태와 일치하지 않습니다.");
    if (value.last_attempt !== null) {
      const attempt = value.last_attempt;
      if (!plainObject(attempt) || !instant(attempt.attempted_at) || !instant(attempt.completed_at) || new Date(attempt.completed_at) < new Date(attempt.attempted_at) || !["success","cached","blocked","failed","partial_success"].includes(attempt.status) || !(attempt.error_code === null || string(attempt.error_code,100)) || !["request_count","eligible_count","completed_count","cached_count","failed_count"].every(key => integer(attempt[key]))) fail("AI 실행 이력을 확인할 수 없습니다.");
      result.last_attempt = {...attempt};
    }
    return result;
  }
  function lastAnalysisAt(data) {
    const times = Object.values(data.items).map(item => item.analyzed_at).filter(instant);
    return times.sort((a,b) => new Date(b)-new Date(a))[0] || null;
  }
  function keyRequiredCount(data) {
    if (!data.last_attempt) return null;
    return Object.values(data.items).filter(item => item.status !== "blocked" && item.status !== "completed" && KEY_CODES.has(item.error_code || data.last_attempt.error_code)).length;
  }
  function summary(data) {
    const keyCount = keyRequiredCount(data);
    const time = lastAnalysisAt(data);
    let label = "수동 실행 준비";
    if (keyCount > 0) label = data.last_attempt.error_code === "free_project_not_confirmed" ? "무료 프로젝트 확인 필요" : "API 키 등록 필요";
    else if (data.counts.stale) label = "재분석 대상 있음";
    else if (data.counts.failed || data.last_attempt?.status === "failed" || data.last_attempt?.status === "partial_success") label = "실행 결과 확인 필요";
    else if (data.counts.completed) label = "AI 제안 확인 가능";
    else if (!data.counts.eligible) label = "현재 분석 가능 자료 없음";
    return {label,lastAnalysisAt:time,keyRequiredCount:keyCount,actualAnalysisExists:time !== null};
  }
  function itemMessage(item) {
    if (item.status === "blocked") return REASONS[item.reason_code] || "현재 AI 이용 범위에서 제외했습니다.";
    if (item.status === "stale") return "분석 후 자료 또는 분석 기준이 바뀌었습니다. 이전 제안은 표시하지 않으며 수동 재분석이 필요합니다.";
    if (KEY_CODES.has(item.error_code)) return item.error_code === "missing_api_key" ? "아직 분석하지 않았습니다. GitHub에 API 키를 등록한 뒤 수동으로 실행해주세요." : "아직 분석하지 않았습니다. 무료 프로젝트 확인 설정을 마친 뒤 수동으로 실행해주세요.";
    if (item.status === "failed") return "분석에 실패했습니다. 기존 수집 자료와 담당자의 선택은 유지됩니다. GitHub 실행 기록을 확인해주세요.";
    if (item.status === "not_analyzed") return "아직 실제 AI 분석 결과가 없습니다. 허용된 공시 서식의 사실 메타데이터만 수동 분석 대상으로 사용할 수 있습니다.";
    return "공시 서식·회사·접수일 등 허용된 사실 메타데이터에 대한 제안입니다. 공시 본문이나 실적 수치를 분석한 결과가 아닙니다.";
  }
  return Object.freeze({validateSidecar,summary,lastAnalysisAt,keyRequiredCount,itemMessage,statusLabels:STATUS_LABELS,relevanceLabels:RELEVANCE});
});
