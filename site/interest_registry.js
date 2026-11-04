/* Read-only public registrations. Applied collection history remains a published artifact. */
(function (root, factory) {
  "use strict";
  const api = factory(typeof module === "object" && module.exports ? require("./interests.js") : root.WeeklyInterests);
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.WeeklyInterestRegistry = api;
})(typeof globalThis === "object" ? globalThis : this, function (Interests) {
  "use strict";
  const API = "https://api.github.com/repos/iesilder/kw-plan-weekly-trend/issues";
  const ISSUE = "https://github.com/iesilder/kw-plan-weekly-trend/issues/";
  const HEADINGS = ["관심 주제","보고 질문","검색어","제외 검색어","분류","시작일","종료일","공개 저장 동의"];
  const CONSENT = "관심사와 검색어가 공개 GitHub 이슈와 웹사이트에 저장됨을 이해합니다.";
  const LABELS = {"실적·자본·주주정책":"performance","신사업·상품·고객전략":"digital","제휴·인수·해외사업":"expansion","조직·경영전략":"strategy","정책·인허가·리스크":"risk"};
  const BASE = ["id","issue_number","title","question","keywords","exclude_keywords","topic_id","active_from","active_until","updated_at","source_url","enabled"];
  const CRITERIA = ["title","question","keywords","exclude_keywords","topic_id","active_from","active_until","enabled"];
  const LIMIT = 2000000, PAGES = 5, TIMEOUT = 15000;
  const SPACE = /[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/g;
  const plain = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const clone = value => JSON.parse(JSON.stringify(value));
  const normalizeSpace = value => value.replace(SPACE," ").replace(/^ +| +$/g,"");
  const optional = value => ["","_No response_"].includes(value.trim()) ? "" : value.trim();
  const MESSAGES = {
    network_error:"GitHub 관심사 등록부를 확인하지 못했습니다. 마지막 확인 목록을 유지합니다.",
    timeout:"관심사 등록부 확인 시간이 초과되었습니다. 잠시 후 다시 확인해 주세요.",
    invalid_response:"GitHub 관심사 등록부의 응답을 확인할 수 없습니다. 마지막 확인 목록을 유지합니다.",
    response_too_large:"관심사 등록부 응답이 허용 크기를 넘었습니다. 마지막 확인 목록을 유지합니다.",
    pagination_limit:"열린 이슈 수가 조회 한도를 넘었습니다. 관심사 목록 전체를 확인하지 못했습니다.",
    invalid_registration:"관심사 등록 양식에 오류가 있습니다. 해당 이슈의 입력 항목을 확인해 주세요.",
    active_limit:"같은 기간에 활성화되는 관심사는 최대 20개입니다. 등록 기간을 확인해 주세요.",
    rate_limited:"GitHub 공개 조회가 일시 제한되었습니다. 표시된 재시도 시간 이후 다시 확인해 주세요."
  };
  function error(code, number = null, retryAt = null) {
    const message = MESSAGES[code] || MESSAGES.invalid_response;
    const result = new Error(number === null ? message : `관심사 #${number}: ${message}`);
    result.code = Object.hasOwn(MESSAGES,code) ? code : "invalid_response";
    if (number !== null) result.issue_number = number;
    if (retryAt !== null) result.retry_at = retryAt;
    return result;
  }
  function validDate(value) {
    if (typeof value !== "string" || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value) || value.startsWith("0000-")) return false;
    const parsed = new Date(`${value}T00:00:00Z`);
    return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0,10) === value;
  }
  function instant(value) {
    if (typeof value !== "string") return false;
    const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$/.exec(value);
    return !!match && validDate(match[1]) && +match[2] < 24 && +match[3] < 60 && +match[4] < 60 && (match[5] === "Z" || (+match[5].slice(1,3) < 24 && +match[5].slice(4) < 60)) && Number.isFinite(Date.parse(value));
  }
  function koreanDate(stamp) {
    if (!instant(stamp)) throw error("invalid_response");
    return new Date(Date.parse(stamp) + 9 * 3600000).toISOString().slice(0,10);
  }
  function state(record, stamp) {
    const today = koreanDate(stamp);
    if (!record.enabled) return "disabled";
    if (record.active_until !== null && record.active_until < today) return "expired";
    return record.active_from <= today ? "active" : "disabled";
  }
  function blankRegistry() { return {status:"never_synced",last_attempt_at:null,last_success_at:null,error_code:null}; }
  function syntheticLatest(generation) { return {schema_version:"1.0",generation_id:generation,is_demo:false,candidates:[]}; }
  function validateRecords(records, stamp) {
    if (!Array.isArray(records) || records.length > 1000 || !instant(stamp)) throw error("invalid_response");
    if (records.some(record => !plain(record) || !validDate(record.active_from) || !(record.active_until === null || validDate(record.active_until)))) throw error("invalid_response");
    const generation = "registration-only";
    const sidecar = {schema_version:"1.0",generation_id:generation,generated_at:stamp,registry:blankRegistry(),last_collection:null,
      interests:records.map(record => ({...record,state:state(record,stamp),last_collected_at:null,collection_status:"never_collected",match_count:0,matches:[]}))};
    // The bound applies across future periods as well as today's active count.
    const events = new Map();
    for (const record of records) {
      if (!plain(record) || !validDate(record.active_from) || !(record.active_until === null || validDate(record.active_until))) throw error("invalid_response");
      if (record.enabled !== true) continue;
      events.set(record.active_from,(events.get(record.active_from) || 0) + 1);
      if (record.active_until !== null && record.active_until !== "9999-12-31") {
        const after = new Date(Date.parse(`${record.active_until}T00:00:00Z`) + 86400000).toISOString().slice(0,10);
        events.set(after,(events.get(after) || 0) - 1);
      }
    }
    let active = 0;
    for (const day of [...events.keys()].sort()) {
      active += events.get(day);
      if (active > 20) throw error("active_limit");
    }
    let validated;
    try { validated = Interests.validateSidecar(sidecar,syntheticLatest(generation)).interests; }
    catch (_) { throw error("invalid_response"); }
    return validated.map(record => Object.fromEntries(BASE.map(key => [key,record[key]])));
  }
  function parseIssue(issue, blankStartIds) {
    const number = issue.number;
    try {
      if (typeof issue.body !== "string" || Array.from(issue.body).length > 12000 || !instant(issue.updated_at) || !instant(issue.created_at)) throw error("invalid_registration",number);
      const headings = [...issue.body.matchAll(/^### ([^\r\n]+)\r?$/gm)];
      if (headings.length !== HEADINGS.length || headings.some((match,index) => match[1] !== HEADINGS[index]) || issue.body.slice(0,headings[0].index).trim()) throw error("invalid_registration",number);
      const sections = Object.fromEntries(headings.map((match,index) => [match[1],issue.body.slice(match.index + match[0].length,index + 1 < headings.length ? headings[index + 1].index : issue.body.length).trim()]));
      if (!["- [X] " + CONSENT,"- [x] " + CONSENT].includes(sections["공개 저장 동의"])) throw error("invalid_registration",number);
      const words = value => optional(value).split(/[,\n]/).map(item => item.trim()).filter(Boolean);
      const blankStart = optional(sections["시작일"]) === "";
      const record = {id:`interest-${number}`,issue_number:number,title:sections["관심 주제"],question:normalizeSpace(sections["보고 질문"]),
        keywords:words(sections["검색어"]),exclude_keywords:words(sections["제외 검색어"]),topic_id:LABELS[sections["분류"]] || sections["분류"],
        active_from:optional(sections["시작일"]) || koreanDate(issue.created_at),active_until:optional(sections["종료일"]) || null,
        updated_at:issue.updated_at,source_url:issue.html_url,enabled:true};
      const validated = validateRecords([record],issue.updated_at)[0];
      if (blankStart) blankStartIds.push(record.id);
      return validated;
    } catch (_) { throw error("invalid_registration",number); }
  }
  function parsePage(payload, seen, blankStartIds) {
    if (!Array.isArray(payload) || payload.length > 100) throw error("invalid_response");
    const records = [];
    for (const issue of payload) {
      if (!plain(issue) || !Number.isSafeInteger(issue.number) || issue.number <= 0 || issue.number > 9999999999 || seen.has(issue.number) || issue.state !== "open") throw error("invalid_response");
      seen.add(issue.number);
      if (Object.hasOwn(issue,"pull_request")) continue;
      if (!plain(issue.user) || typeof issue.user.login !== "string") throw error("invalid_response");
      const registration = issue.user.login === "iesilder" && typeof issue.title === "string" && (issue.title === "[관심사]" || issue.title.startsWith("[관심사] "));
      if (issue.html_url !== ISSUE + issue.number) throw error(registration ? "invalid_registration" : "invalid_response",registration ? issue.number : null);
      if (issue.user.login !== "iesilder") continue;
      if (typeof issue.title !== "string" || Array.from(issue.title).length > 256) throw error("invalid_response");
      if (issue.title !== "[관심사]" && !issue.title.startsWith("[관심사] ")) continue;
      records.push(parseIssue(issue,blankStartIds));
    }
    return records;
  }
  function header(response, name) {
    if (response.headers && typeof response.headers.get === "function") return response.headers.get(name);
    if (plain(response.headers)) {
      const found = Object.keys(response.headers).find(key => key.toLowerCase() === name.toLowerCase());
      return found ? response.headers[found] : null;
    }
    return null;
  }
  function retryTime(response, now) {
    const values = [now + 60000];
    const after = header(response,"Retry-After"), reset = header(response,"X-RateLimit-Reset");
    if (typeof after === "string" && /^\d+(?:\.\d+)?$/.test(after.trim())) values.push(now + Number(after) * 1000);
    else if (typeof after === "string" && Number.isFinite(Date.parse(after))) values.push(Date.parse(after));
    if (typeof reset === "string" && /^\d+$/.test(reset.trim())) values.push(Number(reset) * 1000);
    // Ignore invalid dates, while honoring even unusually long server backoff.
    return Math.max(...values.filter(value => Number.isFinite(value) && value <= 8640000000000000));
  }
  async function readJSON(response) {
    const length = header(response,"Content-Length");
    if (length !== null && /^\d+$/.test(String(length)) && Number(length) > LIMIT) throw error("response_too_large");
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
          if (total > LIMIT) { await reader.cancel(); throw error("response_too_large"); }
          chunks.push(chunk);
        }
      } finally { if (typeof reader.releaseLock === "function") reader.releaseLock(); }
      const bytes = new Uint8Array(total);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk,offset); offset += chunk.byteLength; }
      try { raw = new TextDecoder("utf-8",{fatal:true}).decode(bytes); }
      catch (_) { throw error("invalid_response"); }
    } else {
      if (typeof response.text !== "function") throw error("invalid_response");
      raw = await response.text();
      if (typeof raw !== "string") throw error("invalid_response");
      if (new TextEncoder().encode(raw).byteLength > LIMIT) throw error("response_too_large");
    }
    try { return JSON.parse(raw); }
    catch (_) { throw error("invalid_response"); }
  }
  function createClient({fetchImpl = (...args) => fetch(...args),now = () => new Date(),minimumIntervalMs = 60000} = {}) {
    if (typeof fetchImpl !== "function" || typeof now !== "function" || !Number.isFinite(minimumIntervalMs) || minimumIntervalMs < 0) throw error("invalid_response");
    let cached = null, lastSuccess = -Infinity, inFlight = null, backoffUntil = 0;
    const clock = () => {
      const value = now(), milliseconds = value instanceof Date ? value.getTime() : NaN;
      if (!Number.isFinite(milliseconds)) throw error("invalid_response");
      return milliseconds;
    };
    async function page(number) {
      const controller = new AbortController();
      let timer;
      const timeout = new Promise((_,reject) => { timer = setTimeout(() => { controller.abort(); reject(error("timeout")); },TIMEOUT); });
      const task = (async () => {
        const response = await fetchImpl(`${API}?state=open&creator=iesilder&per_page=100&page=${number}`,{
          method:"GET",credentials:"omit",cache:"no-store",redirect:"error",referrerPolicy:"no-referrer",signal:controller.signal,
          headers:{Accept:"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28"}});
        if (!response || !Number.isInteger(response.status)) throw error("invalid_response");
        if (response.status === 403 || response.status === 429) {
          backoffUntil = Math.max(backoffUntil,retryTime(response,clock()));
          throw error("rate_limited",null,new Date(backoffUntil).toISOString());
        }
        if (response.status !== 200 || response.ok === false) throw error("network_error");
        return readJSON(response);
      })();
      try { return await Promise.race([task,timeout]); }
      catch (failure) {
        controller.abort();
        if (failure && typeof failure.code === "string" && Object.hasOwn(MESSAGES,failure.code)) {
          // Rebuild, rather than exposing messages from an injected transport.
          throw error(failure.code,null,failure.code === "rate_limited" && backoffUntil > 0 ? new Date(backoffUntil).toISOString() : null);
        }
        throw error("network_error");
      } finally { clearTimeout(timer); }
    }
    async function snapshot() {
      const records = [], seen = new Set(), blankStartIds = [];
      for (let number = 1; number <= PAGES; number++) {
        const payload = await page(number);
        records.push(...parsePage(payload,seen,blankStartIds));
        if (payload.length < 100) {
          const completed = clock(), stamp = new Date(completed).toISOString();
          const value = {fetched_at:stamp,interests:validateRecords(records,stamp).sort((a,b) => a.issue_number - b.issue_number),
            ...(blankStartIds.length ? {blank_start_ids:blankStartIds} : {})};
          cached = value;
          lastSuccess = completed;
          return clone(value);
        }
      }
      throw error("pagination_limit");
    }
    function load({force = false} = {}) {
      if (inFlight !== null) return inFlight;
      const current = clock();
      if (current < backoffUntil) return Promise.reject(error("rate_limited",null,new Date(backoffUntil).toISOString()));
      if (!force && cached !== null && current - lastSuccess < minimumIntervalMs) return Promise.resolve(clone(cached));
      const pending = snapshot();
      inFlight = pending.finally(() => { inFlight = null; });
      return inFlight;
    }
    return {load};
  }
  function publishedView(published, latest) {
    if (!plain(published)) return null;
    try {
      if (plain(latest) && latest.is_demo === false && Array.isArray(latest.candidates) && published.generation_id === latest.generation_id) {
        return {value:Interests.validateSidecar(published,latest),canReuse:true};
      }
      // Older generations still establish what configuration was applied, but
      // cannot attach their article IDs or collection caches to a new snapshot.
      const sanitized = {...published,interests:published.interests.map(record => ({...record,matches:[],match_count:0}))};
      return {value:Interests.validateSidecar(sanitized,syntheticLatest(published.generation_id)),canReuse:false};
    } catch (_) {
      // One disappeared article invalidates reuse, not the registration cards.
      try {
        const sanitized = {...published,interests:published.interests.map(record => ({...record,matches:[],match_count:0}))};
        return {value:Interests.validateSidecar(sanitized,syntheticLatest(published.generation_id)),canReuse:false};
      } catch (_) { return null; }
    }
  }
  function mergeRegistry(published, live, latest) {
    if (!plain(live) || !instant(live.fetched_at)) throw error("invalid_response");
    const records = validateRecords(live.interests,live.fetched_at);
    const prior = publishedView(published,latest), applied = prior ? prior.value : null;
    const outdated = applied !== null && applied.registry.last_success_at !== null && Date.parse(live.fetched_at) < Date.parse(applied.registry.last_success_at);
    const stamp = outdated && Date.parse(applied.generated_at) > Date.parse(live.fetched_at) ? applied.generated_at : live.fetched_at;
    const previous = new Map((applied?.interests || []).map(record => [record.id,record]));
    const blanks = live.blank_start_ids === undefined ? [] : live.blank_start_ids;
    if (!Array.isArray(blanks) || new Set(blanks).size !== blanks.length || blanks.length > records.length || blanks.some(id => typeof id !== "string" || !records.some(record => record.id === id))) throw error("invalid_response");
    const current = outdated ? applied.interests.map(record => Object.fromEntries(BASE.map(key => [key,record[key]]))) : records.map(record => {
      const saved = previous.get(record.id);
      // Empty start fields retain an already applied start, matching the batch
      // parser. Only brand-new registrations default to their creation date.
      if (!saved || !blanks.includes(record.id)) return record;
      const normalized = {...record,active_from:saved.active_from};
      if (normalized.active_until !== null && normalized.active_until < normalized.active_from) throw error("invalid_registration",record.issue_number);
      return normalized;
    });
    validateRecords(current,stamp);
    const visible = [...current];
    if (!outdated) for (const record of applied?.interests || []) {
      if (!current.some(item => item.id === record.id)) visible.push({...Object.fromEntries(BASE.map(key => [key,record[key]])),enabled:false});
    }
    const generation = plain(latest) && latest.is_demo === false && typeof latest.generation_id === "string" && latest.generation_id.length <= 100 && latest.generation_id ? latest.generation_id : "registration-only";
    const merged = visible.sort((a,b) => a.issue_number - b.issue_number).map(record => {
      const saved = previous.get(record.id);
      const same = !!saved && CRITERIA.every(key => JSON.stringify(record[key]) === JSON.stringify(saved[key]));
      const currentState = state(record,stamp);
      const newlyActive = same && saved.state !== "active" && currentState === "active";
      const reuse = same && !newlyActive && prior.canReuse && generation === applied.generation_id;
      const status = currentState !== "active" ? "inactive" : !saved || newlyActive ? "pending" : same ? "applied" : "changed";
      return {...record,state:currentState,registration_status:status,
        last_collected_at:reuse ? saved.last_collected_at : null,
        collection_status:currentState !== "active" ? currentState : reuse ? saved.collection_status : !saved || newlyActive ? "pending" : "stale",
        match_count:reuse && currentState === "active" ? saved.matches.length : 0,
        matches:reuse && currentState === "active" ? clone(saved.matches) : []};
    });
    return {schema_version:"1.0",generation_id:generation,generated_at:stamp,registry:applied ? clone(applied.registry) : blankRegistry(),
      last_collection:applied ? clone(applied.last_collection) : null,interests:merged,
      registration_live:!outdated,registry_fetched_at:live.fetched_at,registry_view_status:outdated ? "stale" : "live"};
  }
  return {createClient,mergeRegistry};
});
