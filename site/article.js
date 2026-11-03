/* Article briefs are separate from title classification and human review choices. */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.WeeklyArticle = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";
  const STATUSES = new Set(["not_applicable", "pending", "completed", "stale", "unavailable", "failed"]);
  const PROMPT_VERSIONS = new Set(["article-brief-v1", "article-brief-v2"]);
  const STAGES = new Set(["검토", "추진·협약", "인가·승인", "출시·시행", "성과", "점검·조사", "불명"]);
  const SOURCES = new Map([
    ["연합인포맥스(기사) · 증권", "news.einfomax.co.kr"],
    ["연합인포맥스(기사) · IB·기업", "news.einfomax.co.kr"],
    ["뉴스와이어(보도자료) · 증권", "www.newswire.co.kr"]
  ]);
  const LABELS = Object.freeze({not_applicable:"본문 요약 대상 아님", pending:"본문 분석 대기", completed:"본문 기반 AI 요약", stale:"원자료 변경 · 재분석 필요", unavailable:"본문 확보 실패", failed:"본문 분석 실패"});
  const REASONS = Object.freeze({
    not_article:"현재 기사·보도자료 본문만 요약합니다. 공시 본문과 실적 수치 추출은 아직 연결하지 않았습니다.",
    missing_api_key:"본문 분석을 아직 실행하지 않았습니다. GitHub의 API 키 설정을 확인한 뒤 수동 AI 실행을 진행하세요.",
    free_project_not_confirmed:"본문 분석을 아직 실행하지 않았습니다. 무료 프로젝트 확인 후 수동 AI 실행을 진행하세요.",
    http_forbidden:"출처가 본문 요청을 허용하지 않아 요약하지 못했습니다. 기존 제목 분석과 원문 링크를 이용할 수 있습니다.",
    access_restricted:"로그인·유료 구독 등 접근 제한이 확인되어 본문을 분석하지 않았습니다. 기존 제목 분석과 원문 링크를 이용하세요.",
    body_not_found:"기사 본문 영역을 확인하지 못했습니다. 제목 분석을 유지하며 본문을 읽은 요약은 표시하지 않습니다.",
    body_too_short:"확보한 본문이 충분하지 않아 요약하지 않았습니다. 원문 링크로 내용을 확인하세요.",
    rate_limited:"AI 사용 한도에 도달해 본문 분석을 마치지 못했습니다. 기존 수집 자료와 검토 선택은 유지됩니다.",
    invalid_output:"AI 요약의 형식이나 원문 근거를 확인하지 못해 결과를 표시하지 않습니다.",
    output_number_mismatch:"요약에 원문에서 확인되지 않는 수치가 포함되어 이 자료의 결과를 표시하지 않습니다.",
    output_evidence_mismatch:"요약의 주장과 참조 문단을 연결하지 못해 이 자료의 결과를 표시하지 않습니다.",
    output_invalid_shape:"이 자료의 AI 응답 형식을 확인하지 못해 요약을 표시하지 않습니다.",
    output_missing_item:"AI 응답에 이 자료의 요약이 없어 결과를 표시하지 않습니다.",
    output_duplicate_item:"AI 응답에서 이 자료가 중복되어 결과를 확인할 수 없습니다.",
    output_quote_invalid:"이 자료의 참조 문단 발췌를 검증하지 못해 요약을 표시하지 않습니다."
  });
  const ISSUE = /^issue-[a-f0-9]{20}$/;
  const VERSION = /^version-[a-f0-9]{20}$/;
  const HASH = /^[a-f0-9]{64}$/;
  const PARAGRAPH = /^p[1-9][0-9]{0,2}$/;
  const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const text = (value,max) => typeof value === "string" && !!value.trim() && value.length <= max && !/[\u0000-\u001f\u007f]/.test(value);
  const array = (value, check, max=1000) => Array.isArray(value) && value.length <= max && value.every(check);
  const same = (left,right) => [...left].sort().join("|") === [...right].sort().join("|");
  const integer = value => Number.isSafeInteger(value) && value >= 0;
  function fail(message) { throw new Error(message); }
  function instant(value) {
    if (typeof value !== "string") return false;
    const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$/.exec(value);
    if (!match || +match[2]>23 || +match[3]>59 || +match[4]>59) return false;
    const date = new Date(`${match[1]}T00:00:00Z`);
    if (Number.isNaN(date.getTime()) || date.toISOString().slice(0,10)!==match[1]) return false;
    if (match[5]!=="Z" && (+match[5].slice(1,3)>23 || +match[5].slice(4)>59)) return false;
    return !Number.isNaN(new Date(value).getTime());
  }
  function articleURL(value, sourceName) {
    if (typeof value !== "string" || value.length > 4096 || /[\u0000-\u0020\u007f\\]/.test(value)) return null;
    try {
      const url = new URL(value);
      if (url.protocol!=="https:" || url.username || url.password || url.port || url.hostname!==SOURCES.get(sourceName)) return null;
      const key = url.hostname==="news.einfomax.co.kr" ? "idxno" : "no";
      const path = key==="idxno" ? "/news/articleView.html" : "/newsRead.php";
      if (url.pathname!==path || url.searchParams.getAll(key).length!==1 || [...url.searchParams.keys()].some(name=>name!==key) || !/^[1-9][0-9]{0,11}$/.test(url.searchParams.get(key)||"")) return null;
      return `https://${url.hostname}${path}?${key}=${url.searchParams.get(key)}`;
    } catch { return null; }
  }
  function validateResult(value) {
    if (!object(value) || !STAGES.has(value.stage) || !text(value.why_candidate,240) || !array(value.evidence, row=>object(row)&&PARAGRAPH.test(row.id)&&text(row.quote,80),4) || !value.evidence.length) fail("본문 요약의 원문 근거 형식을 확인할 수 없습니다.");
    const ids = new Set(value.evidence.map(row=>row.id));
    if (ids.size!==value.evidence.length || value.evidence.reduce((sum,row)=>sum+row.quote.length,0)>200) fail("본문 요약의 인용 범위나 문단 번호가 올바르지 않습니다.");
    function claim(row,max) {
      if (!object(row) || !text(row.text,max) || !array(row.evidence_ids,id=>PARAGRAPH.test(id)&&ids.has(id),4) || !row.evidence_ids.length || new Set(row.evidence_ids).size!==row.evidence_ids.length) fail("본문 요약의 주장과 원문 근거가 연결되지 않았습니다.");
      return {text:row.text,evidence_ids:[...row.evidence_ids]};
    }
    if (!array(value.facts,row=>object(row),4)) fail("본문 요약의 주요 사실 형식이 올바르지 않습니다.");
    return {key_change:claim(value.key_change,260),facts:value.facts.map(row=>claim(row,180)),stage:value.stage,why_candidate:value.why_candidate,evidence:value.evidence.map(row=>({id:row.id,quote:row.quote}))};
  }
  function validateSidecar(value, latest) {
    if (!object(value) || value.schema_version!=="1.0" || !latest || latest.is_demo!==false || !Array.isArray(latest.candidates)) fail("본문 요약 자료의 형식을 확인할 수 없습니다.");
    if (value.generation_id!==latest.generation_id) fail("본문 요약과 현재 수집 자료의 버전이 다릅니다.");
    if (!instant(value.generated_at) || value.provider!=="google_gemini" || value.model!=="gemini-3.5-flash-lite" || !PROMPT_VERSIONS.has(value.prompt_version) || value.auto_analysis_enabled!==false || !object(value.items) || !object(value.counts)) fail("본문 요약의 생성 정보나 실행 설정이 올바르지 않습니다.");
    const candidates = new Map(latest.candidates.map(item=>[item.issue_id,item]));
    if (candidates.size!==latest.candidates.length || !same([...candidates.keys()],Object.keys(value.items))) fail("본문 요약의 대상 목록이 현재 수집 자료와 다릅니다.");
    const result = {schema_version:"1.0",generation_id:value.generation_id,generated_at:value.generated_at,provider:value.provider,model:value.model,prompt_version:value.prompt_version,auto_analysis_enabled:false,counts:{eligible:0,completed:0,stale:0,unavailable:0,pending:0,not_applicable:0,failed:0},last_attempt:null,items:{}};
    for (const [id,item] of Object.entries(value.items)) {
      const candidate = candidates.get(id);
      if (!ISSUE.test(id) || !object(item) || !STATUSES.has(item.status) || !(item.reason_code===null||text(item.reason_code,100)) || !array(item.evidence_ids,value=>typeof value==="string"&&VERSION.test(value)) || new Set(item.evidence_ids).size!==item.evidence_ids.length || !array(candidate.evidence_ids,id=>typeof id==="string") || !same(item.evidence_ids,candidate.evidence_ids) || typeof item.truncated!=="boolean") fail("개별 본문 요약의 상태나 원자료 근거가 올바르지 않습니다.");
      if (candidate.is_demo!==false) fail("가상 자료에는 실제 본문 요약을 연결하지 않습니다.");
      result.counts[item.status]++;
      if (item.status!=="not_applicable") {
        result.counts.eligible++;
        if (candidate.content_filter!=="topics" || !SOURCES.has(candidate.source_name) || item.source_name!==candidate.source_name || !articleURL(item.source_url,item.source_name)) fail("본문 요약의 출처나 원문 주소를 확인할 수 없습니다.");
      }
      for (const field of ["input_fingerprint","result_fingerprint","body_hash"]) if (!(item[field]===null||HASH.test(item[field]||""))) fail("본문 요약의 입력 버전이 올바르지 않습니다.");
      for (const field of ["analyzed_at","body_fetched_at"]) if (!(item[field]===null||instant(item[field]))) fail("본문 요약의 분석·수집 시각이 올바르지 않습니다.");
      let brief = null;
      if (item.status==="completed") {
        if (!HASH.test(item.input_fingerprint||"") || !HASH.test(item.result_fingerprint||"") || !HASH.test(item.body_hash||"") || !instant(item.analyzed_at) || !instant(item.body_fetched_at) || (item.status==="completed" && item.input_fingerprint!==item.result_fingerprint) || item.reason_code!==null) fail("현재 본문과 요약 결과의 버전을 확인할 수 없습니다.");
        brief = validateResult(item.result);
      } else if (item.status==="stale") {
        if (item.result!==null) fail("재분석 대상에는 과거 본문 요약을 표시하지 않습니다.");
      } else if (item.result!==null || item.result_fingerprint!==null || item.analyzed_at!==null) fail("본문 분석이 완료되지 않은 자료에 요약 결과가 포함되어 있습니다.");
      result.items[id] = {status:item.status,reason_code:item.reason_code,evidence_ids:[...item.evidence_ids],input_fingerprint:item.input_fingerprint,result_fingerprint:item.result_fingerprint,analyzed_at:item.analyzed_at,source_url:item.source_url,source_name:item.source_name,body_hash:item.body_hash,body_fetched_at:item.body_fetched_at,truncated:item.truncated,result:brief};
    }
    for (const [key,count] of Object.entries(result.counts)) if (value.counts[key]!==count) fail("본문 요약의 처리 건수가 자료별 상태와 일치하지 않습니다.");
    if (value.last_attempt!==null) {
      const attempt = value.last_attempt;
      if (!object(attempt) || !instant(attempt.attempted_at) || !instant(attempt.completed_at) || new Date(attempt.completed_at)<new Date(attempt.attempted_at) || !["success","cached","partial_success","blocked","failed","prepared"].includes(attempt.status) || !(attempt.error_code===null||text(attempt.error_code,100)) || !["request_count","fetch_count","completed_count","cached_count","failed_count"].every(key=>integer(attempt[key])) || attempt.request_count>1 || attempt.fetch_count>5 || attempt.completed_count>5 || attempt.cached_count>10000) fail("본문 분석 실행 이력의 형식이 올바르지 않습니다.");
      result.last_attempt = {...attempt};
    }
    return result;
  }
  function matchesDetail(item, detail) {
    if (!item || !detail || !Array.isArray(detail.evidence) || !same(item.evidence_ids,detail.evidence.map(row=>row.evidence_id))) return false;
    if (item.status==="not_applicable") return true;
    const canonical = articleURL(item.source_url,item.source_name);
    return canonical!==null && detail.evidence.some(row=>row.source_name===item.source_name&&articleURL(row.source_url,row.source_name)===canonical);
  }
  function itemMessage(item) {
    if (item.status==="completed") return item.truncated ? "확보한 본문의 일부를 분석한 AI 요약입니다. 기사 전체를 읽은 결과가 아니므로 원문을 함께 확인하세요." : "확보한 기사 본문에 근거한 AI 요약입니다. 원문 근거와 담당자의 판단을 함께 확인하세요.";
    if (item.status==="stale") return "수집 자료 또는 분석 기준이 바뀌어 이전 본문 요약을 표시하지 않습니다. 수동 재분석이 필요합니다.";
    if (REASONS[item.reason_code]) return REASONS[item.reason_code];
    if (item.status==="not_applicable") return REASONS.not_article;
    if (item.status==="unavailable") return "본문을 확보하지 못해 요약을 표시하지 않습니다. 제목 분석과 원문 링크는 계속 이용할 수 있습니다.";
    if (item.status==="failed") return "본문 분석을 마치지 못했습니다. 기존 제목 분석과 담당자의 선택은 유지됩니다.";
    return "아직 본문 기반 요약이 없습니다. 수동 AI 실행에서 공개 본문을 확보한 뒤 분석합니다.";
  }
  function summary(data) {
    let label="본문 분석 대기";
    if (data.counts.failed) label="일부 분석 실패";
    else if (data.counts.stale) label="재분석 대상 있음";
    else if (data.counts.completed) label="본문 요약 확인 가능";
    else if (data.counts.unavailable) label="본문 확보 필요";
    else if (!data.counts.eligible) label="현재 기사 없음";
    return {label,lastAnalysisAt:Object.values(data.items).filter(item=>item.status==="completed").map(item=>item.analyzed_at).sort((a,b)=>new Date(b)-new Date(a))[0]||null};
  }
  return Object.freeze({validateSidecar,validateResult,articleURL,matchesDetail,itemMessage,summary,statusLabels:LABELS});
});
