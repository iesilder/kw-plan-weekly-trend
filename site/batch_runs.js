/* Public Actions execution history. This is separate from saved collection data. */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.WeeklyBatchRuns = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";
  const REPO = "iesilder/kw-plan-weekly-trend";
  const ACTIONS_URL = `https://github.com/${REPO}/actions`;
  const API = `https://api.github.com/repos/${REPO}/actions/workflows/`;
  const WORKFLOWS = {daily:"daily.yml",pipeline:"pipeline.yml",interests:"interests.yml",rss:"rss.yml"};
  const KIND_LABELS = {daily_scheduled:"정기 자동 배치",daily_manual:"정기 배치 수동 실행",manual_collection:"일반 수동 수집",interest_manual:"관심사 요청 실행",interest_rebuild:"관심사 저장 자료 재분류",rss_scheduled:"RSS 정기 저장",rss_manual:"RSS 수동 저장",rss_bootstrap:"수집 구조 변경 후 갱신"};
  const STATE_LABELS = {queued:"대기 중",running:"실행 중",success:"성공",failure:"실패",cancelled:"취소",skipped:"실행 생략",unknown:"확인 불가"};
  const MESSAGES = {network_error:"GitHub 실행 기록을 확인하지 못했습니다.",timeout:"실행 기록 확인 시간이 초과되었습니다.",invalid_response:"GitHub 실행 기록의 응답을 확인할 수 없습니다.",response_too_large:"실행 기록 응답이 조회 크기 한도를 넘었습니다.",pagination_limit:"최근 실행이 조회 한도를 넘어 전체 기록을 확인하지 못했습니다.",rate_limited:"GitHub 공개 조회가 일시 제한되었습니다. 잠시 후 다시 확인해 주세요."};
  const DAY = 86400000, CACHE = 300000, MINIMUM = 60000, LIMIT = 2000000, PAGES = 3, RSS_PAGES = 10, TIMEOUT = 15000;
  const plain = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const clone = value => JSON.parse(JSON.stringify(value));
  const safeInteger = value => Number.isSafeInteger(value) && value > 0;
  function milliseconds(value) {
    const result = value instanceof Date ? value.getTime() : typeof value === "number" ? value : typeof value === "string" ? Date.parse(value) : NaN;
    if (!Number.isFinite(result) || Math.abs(result) > 8.64e15) throw new TypeError("유효한 확인 시각이 필요합니다.");
    return result;
  }
  function stamp(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
    const parsed = Date.parse(value);
    if (!Number.isFinite(parsed)) return null;
    const day = value.slice(0,10), date = new Date(`${day}T00:00:00Z`);
    if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0,10) !== day || +value.slice(11,13) > 23 || +value.slice(14,16) > 59 || +value.slice(17,19) > 59) return null;
    return new Date(parsed).toISOString();
  }
  const dayKst = value => new Date(milliseconds(value) + 9 * 3600000).toISOString().slice(0,10);
  function failure(code, retryAt = null) {
    const e = new Error(MESSAGES[code] || MESSAGES.invalid_response);
    e.code = Object.hasOwn(MESSAGES,code) ? code : "invalid_response";
    e.retryAt = retryAt;
    return e;
  }
  const safeError = e => ({code:Object.hasOwn(MESSAGES,e?.code) ? e.code : "network_error",message:MESSAGES[e?.code] || MESSAGES.network_error,retryAt:typeof e?.retryAt === "string" && stamp(e.retryAt) ? stamp(e.retryAt) : null});
  function normalizeRun(run, workflow) {
    if (!plain(run) || !safeInteger(run.id) || !safeInteger(run.run_attempt) || run.run_attempt > 10000 || typeof run.event !== "string" || typeof run.status !== "string") throw failure("invalid_response");
    const expectedPath = `.github/workflows/${WORKFLOWS[workflow]}`;
    if (![expectedPath,`${expectedPath}@main`,`${expectedPath}@refs/heads/main`].includes(run.path) || run.head_branch !== "main" || run.html_url !== `${ACTIONS_URL}/runs/${run.id}` || (run.repository !== undefined && (!plain(run.repository) || typeof run.repository.full_name !== "string" || run.repository.full_name.toLowerCase() !== REPO))) throw failure("invalid_response");
    const createdAt = stamp(run.created_at), updatedAt = stamp(run.updated_at), sourceStartedAt = run.run_started_at == null ? null : stamp(run.run_started_at);
    if (!createdAt || !updatedAt || (run.run_started_at != null && !sourceStartedAt)) throw failure("invalid_response");
    let kind;
    if (workflow === "daily" && run.event === "schedule") kind = "daily_scheduled";
    else if (workflow === "daily" && run.event === "workflow_dispatch") kind = "daily_manual";
    else if (workflow === "pipeline" && run.event === "workflow_dispatch") kind = "manual_collection";
    else if (workflow === "rss" && run.event === "schedule") kind = "rss_scheduled";
    else if (workflow === "rss" && run.event === "workflow_dispatch") kind = "rss_manual";
    else if (workflow === "rss" && run.event === "push") kind = "rss_bootstrap";
    else if (workflow === "interests" && run.event === "workflow_dispatch") kind = "interest_manual";
    else if (workflow === "interests" && run.event === "issues") {
      if (run.conclusion === "skipped") return null;
      kind = typeof run.display_title === "string" && /^\[관심사(?: 재분류)?\](?:\s|$)/.test(run.display_title) ? "interest_rebuild" : "interest_manual";
    } else return null;
    let state = "unknown";
    if (["queued","waiting","pending","requested"].includes(run.status)) state = "queued";
    else if (run.status === "in_progress") state = "running";
    else if (run.status === "completed") {
      if (["success","failure","cancelled","skipped"].includes(run.conclusion)) state = run.conclusion;
      else if (["timed_out","action_required","startup_failure"].includes(run.conclusion)) state = "failure";
    }
    return {id:run.id,attempt:run.run_attempt,workflow,kind,label:KIND_LABELS[kind],event:run.event,state,createdAt,startedAt:state === "queued" ? null : sourceStartedAt,updatedAt,url:`${ACTIONS_URL}/runs/${run.id}/attempts/${run.run_attempt}`};
  }
  function header(response,name) {
    if (response.headers && typeof response.headers.get === "function") return response.headers.get(name);
    return null;
  }
  function retryTime(response,now) {
    const after = header(response,"Retry-After"), reset = header(response,"X-RateLimit-Reset"), times = [now + MINIMUM];
    if (typeof after === "string" && /^\d+(?:\.\d+)?$/.test(after.trim())) times.push(now + Number(after) * 1000);
    else if (typeof after === "string" && Number.isFinite(Date.parse(after))) times.push(Date.parse(after));
    if (typeof reset === "string" && /^\d+$/.test(reset.trim())) times.push(Number(reset) * 1000);
    return Math.max(...times.filter(value => Number.isFinite(value) && Math.abs(value) <= 8.64e15));
  }
  async function readJSON(response) {
    const length = header(response,"Content-Length");
    if (length !== null && /^\d+$/.test(String(length)) && Number(length) > LIMIT) throw failure("response_too_large");
    let raw;
    if (response.body && typeof response.body.getReader === "function") {
      const reader = response.body.getReader(), chunks = [];
      let total = 0;
      try {
        while (true) {
          const item = await reader.read();
          if (item.done) break;
          const chunk = item.value instanceof Uint8Array ? item.value : new Uint8Array(item.value);
          total += chunk.byteLength;
          if (total > LIMIT) { await reader.cancel(); throw failure("response_too_large"); }
          chunks.push(chunk);
        }
      } finally { if (typeof reader.releaseLock === "function") reader.releaseLock(); }
      const bytes = new Uint8Array(total);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk,offset); offset += chunk.byteLength; }
      try { raw = new TextDecoder("utf-8",{fatal:true}).decode(bytes); } catch (_) { throw failure("invalid_response"); }
    } else {
      if (typeof response.text !== "function") throw failure("invalid_response");
      raw = await response.text();
      if (typeof raw !== "string") throw failure("invalid_response");
      if (new TextEncoder().encode(raw).byteLength > LIMIT) throw failure("response_too_large");
    }
    try { return JSON.parse(raw); } catch (_) { throw failure("invalid_response"); }
  }
  function deriveToday(model, now = Date.now()) {
    const at = milliseconds(now), dateKst = dayKst(at), scheduledAt = `${dateKst}T02:00:00.000Z`;
    const daily = model?.workflows?.daily;
    const records = Array.isArray(daily?.records) ? daily.records : [];
    const record = records.filter(run => run.kind === "daily_scheduled" && dayKst(run.createdAt) === dateKst).sort((a,b) => Date.parse(b.createdAt) - Date.parse(a.createdAt) || b.attempt - a.attempt)[0] || null;
    const checkedAt = stamp(daily?.checkedAt), checked = checkedAt ? Date.parse(checkedAt) : NaN;
    const current = daily?.complete === true && Number.isFinite(checked) && dayKst(checked) === dateKst && checked <= at && at - checked <= CACHE;
    // A pre-11:00 empty lookup cannot prove that today's scheduled run is absent after 11:00.
    const sufficient = current && (at < Date.parse(scheduledAt) || checked >= Date.parse(scheduledAt) || record !== null);
    return {dateKst,scheduledAt,state:!sufficient ? "unknown" : record ? record.state : at < Date.parse(scheduledAt) ? "waiting" : "no_record",record,checkedAt};
  }
  function createClient({fetchImpl = (...args) => fetch(...args),now = () => Date.now()} = {}) {
    if (typeof fetchImpl !== "function" || typeof now !== "function") throw new TypeError("실행 기록 조회 설정을 확인해 주세요.");
    const clock = () => milliseconds(now());
    let cached = null, inFlight = null, lastAttempt = -Infinity, backoffUntil = 0;
    async function request(workflow,page,cutoff) {
      const controller = new AbortController();
      let timer;
      const timeout = new Promise((_,reject) => { timer = setTimeout(() => { controller.abort(); reject(failure("timeout")); },TIMEOUT); });
      const task = (async () => {
        const query = new URLSearchParams({branch:"main",per_page:"100",page:String(page),created:`>=${cutoff}`,exclude_pull_requests:"true"});
        const response = await fetchImpl(`${API}${WORKFLOWS[workflow]}/runs?${query}`,{method:"GET",credentials:"omit",cache:"no-store",redirect:"error",referrerPolicy:"no-referrer",signal:controller.signal,headers:{Accept:"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28"}});
        if (!response || !Number.isInteger(response.status)) throw failure("invalid_response");
        if (response.status === 403 || response.status === 429) {
          backoffUntil = Math.max(backoffUntil,retryTime(response,clock()));
          throw failure("rate_limited",new Date(backoffUntil).toISOString());
        }
        if (response.status !== 200 || response.ok === false) throw failure("network_error");
        return readJSON(response);
      })();
      try { return await Promise.race([task,timeout]); }
      catch (e) { controller.abort(); throw failure(Object.hasOwn(MESSAGES,e?.code) ? e.code : "network_error",e?.retryAt || null); }
      finally { clearTimeout(timer); }
    }
    async function workflowSnapshot(workflow,cutoff) {
      const lookupAt = new Date(clock()).toISOString();
      const records = [], seen = new Set();
      let expected = null, received = 0;
      try {
        // An hourly feed has roughly 720 runs in 30 days; keep this bounded
        // separately from less frequent daily/manual workflows.
        const maxPages = workflow === "rss" ? RSS_PAGES : PAGES;
        for (let page = 1; page <= maxPages; page++) {
          const data = await request(workflow,page,cutoff);
          if (!plain(data) || !Number.isSafeInteger(data.total_count) || data.total_count < 0 || !Array.isArray(data.workflow_runs) || data.workflow_runs.length > 100) throw failure("invalid_response");
          if (expected !== null && expected !== data.total_count) throw failure("pagination_limit");
          expected = data.total_count;
          for (const run of data.workflow_runs) {
            const record = normalizeRun(run,workflow), key = run.id;
            if (seen.has(key)) throw failure("invalid_response");
            seen.add(key); received++;
            if (record && Date.parse(record.createdAt) >= Date.parse(cutoff)) records.push(record);
          }
          if (received > expected) throw failure("invalid_response");
          if (received === expected) return {complete:true,checkedAt:lookupAt,error:null,records};
          if (data.workflow_runs.length < 100) throw failure("pagination_limit");
        }
        throw failure("pagination_limit");
      } catch (e) {
        const prior = cached?.workflows?.[workflow];
        // Keep dated previous records when lookup fails, but never claim complete coverage.
        const merged = new Map((prior?.records || []).map(run => [run.id,run]));
        for (const record of records) {
          const previous = merged.get(record.id);
          if (!previous || record.attempt >= previous.attempt) merged.set(record.id,record);
        }
        return {complete:false,checkedAt:prior?.checkedAt || null,error:safeError(e),records:[...merged.values()]};
      }
    }
    function finish(workflows,checkedAt,cachedResult) {
      const executionTime = run => Date.parse(run.startedAt || (run.state === "queued" ? run.updatedAt : null) || run.createdAt);
      const records = Object.values(workflows).flatMap(value => value.records).sort((a,b) => executionTime(b) - executionTime(a) || b.attempt - a.attempt || b.id - a.id);
      const messages = [...new Set(Object.values(workflows).filter(value => value.error).map(value => value.error.message))];
      return {records,checkedAt,workflows,warning:messages.join(" "),cached:cachedResult};
    }
    function cooldown(at) {
      const workflows = Object.fromEntries(Object.keys(WORKFLOWS).map(key => [key,{complete:false,checkedAt:cached?.workflows?.[key]?.checkedAt || null,error:safeError(failure("rate_limited",new Date(backoffUntil).toISOString())),records:cached?.workflows?.[key]?.records || []}]));
      return finish(workflows,cached?.checkedAt || new Date(at).toISOString(),true);
    }
    return {load({force = false} = {}) {
      const at = clock();
      if (inFlight) return inFlight.then(clone);
      if (at < backoffUntil) return Promise.resolve(clone(cooldown(at)));
      const complete = cached && Object.values(cached.workflows).every(value => value.complete);
      if (cached && at >= lastAttempt && at - lastAttempt < (force || !complete ? MINIMUM : CACHE)) return Promise.resolve({...clone(cached),cached:true});
      lastAttempt = at;
      // 30 Korean calendar dates including today; the same cutoff applies to all pages.
      const cutoff = new Date(Date.parse(`${dayKst(at)}T00:00:00+09:00`) - 29 * DAY).toISOString();
      inFlight = Promise.all(Object.keys(WORKFLOWS).map(async workflow => [workflow,await workflowSnapshot(workflow,cutoff)]))
        .then(entries => { cached = finish(Object.fromEntries(entries),new Date(clock()).toISOString(),false); return cached; })
        .finally(() => { inFlight = null; });
      return inFlight.then(clone);
    }};
  }
  return {createClient,deriveToday,normalizeRun,ACTIONS_URL,KIND_LABELS,STATE_LABELS};
});
