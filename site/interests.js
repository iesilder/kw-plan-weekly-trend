/* Public interest registry and title-keyword matches, independent of AI and review choices. */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.WeeklyInterests = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";
  const REPO = "https://github.com/iesilder/kw-plan-weekly-trend";
  const TOPICS = new Set(["performance", "digital", "expansion", "strategy", "risk"]);
  const NEWS = new Set(["연합인포맥스(기사) · 증권", "연합인포맥스(기사) · IB·기업", "뉴스와이어(보도자료) · 증권"]);
  const COLLECTION = new Set(["never_collected", "success", "partial_success", "failed", "pending", "stale", "disabled", "expired"]);
  const STATUS_LABELS = Object.freeze({never_collected:"수집 전",success:"수집 완료",partial_success:"일부 출처 수집 실패",failed:"수집 실패",pending:"수집 대기",stale:"조건 변경 · 재수집 필요",disabled:"사용 안 함 · 시작 전",expired:"기간 종료"});
  const plain = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const SPACE = /[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/g;
  const trim = value => value.replace(SPACE," ").replace(/^ +| +$/g,"");
  const text = (value,max) => typeof value === "string" && !!value && Array.from(value).length <= max && value === value.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/g,"") && !/[\u0000-\u001f\u007f]/.test(value);
  // Case-fold expansions that differ from lower-case, matching the Python collector.
  const CASE_FOLD = {"µ":"μ","ß":"ss","ŉ":"ʼn","ſ":"s","ǰ":"ǰ","ͅ":"ι","ΐ":"ΐ","ΰ":"ΰ","ς":"σ","ϐ":"β","ϑ":"θ","ϕ":"φ","ϖ":"π","ϰ":"κ","ϱ":"ρ","ϵ":"ε","և":"եւ","Ꭰ":"Ꭰ","Ꭱ":"Ꭱ","Ꭲ":"Ꭲ","Ꭳ":"Ꭳ","Ꭴ":"Ꭴ","Ꭵ":"Ꭵ","Ꭶ":"Ꭶ","Ꭷ":"Ꭷ","Ꭸ":"Ꭸ","Ꭹ":"Ꭹ","Ꭺ":"Ꭺ","Ꭻ":"Ꭻ","Ꭼ":"Ꭼ","Ꭽ":"Ꭽ","Ꭾ":"Ꭾ","Ꭿ":"Ꭿ","Ꮀ":"Ꮀ","Ꮁ":"Ꮁ","Ꮂ":"Ꮂ","Ꮃ":"Ꮃ","Ꮄ":"Ꮄ","Ꮅ":"Ꮅ","Ꮆ":"Ꮆ","Ꮇ":"Ꮇ","Ꮈ":"Ꮈ","Ꮉ":"Ꮉ","Ꮊ":"Ꮊ","Ꮋ":"Ꮋ","Ꮌ":"Ꮌ","Ꮍ":"Ꮍ","Ꮎ":"Ꮎ","Ꮏ":"Ꮏ","Ꮐ":"Ꮐ","Ꮑ":"Ꮑ","Ꮒ":"Ꮒ","Ꮓ":"Ꮓ","Ꮔ":"Ꮔ","Ꮕ":"Ꮕ","Ꮖ":"Ꮖ","Ꮗ":"Ꮗ","Ꮘ":"Ꮘ","Ꮙ":"Ꮙ","Ꮚ":"Ꮚ","Ꮛ":"Ꮛ","Ꮜ":"Ꮜ","Ꮝ":"Ꮝ","Ꮞ":"Ꮞ","Ꮟ":"Ꮟ","Ꮠ":"Ꮠ","Ꮡ":"Ꮡ","Ꮢ":"Ꮢ","Ꮣ":"Ꮣ","Ꮤ":"Ꮤ","Ꮥ":"Ꮥ","Ꮦ":"Ꮦ","Ꮧ":"Ꮧ","Ꮨ":"Ꮨ","Ꮩ":"Ꮩ","Ꮪ":"Ꮪ","Ꮫ":"Ꮫ","Ꮬ":"Ꮬ","Ꮭ":"Ꮭ","Ꮮ":"Ꮮ","Ꮯ":"Ꮯ","Ꮰ":"Ꮰ","Ꮱ":"Ꮱ","Ꮲ":"Ꮲ","Ꮳ":"Ꮳ","Ꮴ":"Ꮴ","Ꮵ":"Ꮵ","Ꮶ":"Ꮶ","Ꮷ":"Ꮷ","Ꮸ":"Ꮸ","Ꮹ":"Ꮹ","Ꮺ":"Ꮺ","Ꮻ":"Ꮻ","Ꮼ":"Ꮼ","Ꮽ":"Ꮽ","Ꮾ":"Ꮾ","Ꮿ":"Ꮿ","Ᏸ":"Ᏸ","Ᏹ":"Ᏹ","Ᏺ":"Ᏺ","Ᏻ":"Ᏻ","Ᏼ":"Ᏼ","Ᏽ":"Ᏽ","ᏸ":"Ᏸ","ᏹ":"Ᏹ","ᏺ":"Ᏺ","ᏻ":"Ᏻ","ᏼ":"Ᏼ","ᏽ":"Ᏽ","ᲀ":"в","ᲁ":"д","ᲂ":"о","ᲃ":"с","ᲄ":"т","ᲅ":"т","ᲆ":"ъ","ᲇ":"ѣ","ᲈ":"ꙋ","ẖ":"ẖ","ẗ":"ẗ","ẘ":"ẘ","ẙ":"ẙ","ẚ":"aʾ","ẛ":"ṡ","ẞ":"ss","ὐ":"ὐ","ὒ":"ὒ","ὔ":"ὔ","ὖ":"ὖ","ᾀ":"ἀι","ᾁ":"ἁι","ᾂ":"ἂι","ᾃ":"ἃι","ᾄ":"ἄι","ᾅ":"ἅι","ᾆ":"ἆι","ᾇ":"ἇι","ᾈ":"ἀι","ᾉ":"ἁι","ᾊ":"ἂι","ᾋ":"ἃι","ᾌ":"ἄι","ᾍ":"ἅι","ᾎ":"ἆι","ᾏ":"ἇι","ᾐ":"ἠι","ᾑ":"ἡι","ᾒ":"ἢι","ᾓ":"ἣι","ᾔ":"ἤι","ᾕ":"ἥι","ᾖ":"ἦι","ᾗ":"ἧι","ᾘ":"ἠι","ᾙ":"ἡι","ᾚ":"ἢι","ᾛ":"ἣι","ᾜ":"ἤι","ᾝ":"ἥι","ᾞ":"ἦι","ᾟ":"ἧι","ᾠ":"ὠι","ᾡ":"ὡι","ᾢ":"ὢι","ᾣ":"ὣι","ᾤ":"ὤι","ᾥ":"ὥι","ᾦ":"ὦι","ᾧ":"ὧι","ᾨ":"ὠι","ᾩ":"ὡι","ᾪ":"ὢι","ᾫ":"ὣι","ᾬ":"ὤι","ᾭ":"ὥι","ᾮ":"ὦι","ᾯ":"ὧι","ᾲ":"ὰι","ᾳ":"αι","ᾴ":"άι","ᾶ":"ᾶ","ᾷ":"ᾶι","ᾼ":"αι","ι":"ι","ῂ":"ὴι","ῃ":"ηι","ῄ":"ήι","ῆ":"ῆ","ῇ":"ῆι","ῌ":"ηι","ῒ":"ῒ","ΐ":"ΐ","ῖ":"ῖ","ῗ":"ῗ","ῢ":"ῢ","ΰ":"ΰ","ῤ":"ῤ","ῦ":"ῦ","ῧ":"ῧ","ῲ":"ὼι","ῳ":"ωι","ῴ":"ώι","ῶ":"ῶ","ῷ":"ῶι","ῼ":"ωι","ꭰ":"Ꭰ","ꭱ":"Ꭱ","ꭲ":"Ꭲ","ꭳ":"Ꭳ","ꭴ":"Ꭴ","ꭵ":"Ꭵ","ꭶ":"Ꭶ","ꭷ":"Ꭷ","ꭸ":"Ꭸ","ꭹ":"Ꭹ","ꭺ":"Ꭺ","ꭻ":"Ꭻ","ꭼ":"Ꭼ","ꭽ":"Ꭽ","ꭾ":"Ꭾ","ꭿ":"Ꭿ","ꮀ":"Ꮀ","ꮁ":"Ꮁ","ꮂ":"Ꮂ","ꮃ":"Ꮃ","ꮄ":"Ꮄ","ꮅ":"Ꮅ","ꮆ":"Ꮆ","ꮇ":"Ꮇ","ꮈ":"Ꮈ","ꮉ":"Ꮉ","ꮊ":"Ꮊ","ꮋ":"Ꮋ","ꮌ":"Ꮌ","ꮍ":"Ꮍ","ꮎ":"Ꮎ","ꮏ":"Ꮏ","ꮐ":"Ꮐ","ꮑ":"Ꮑ","ꮒ":"Ꮒ","ꮓ":"Ꮓ","ꮔ":"Ꮔ","ꮕ":"Ꮕ","ꮖ":"Ꮖ","ꮗ":"Ꮗ","ꮘ":"Ꮘ","ꮙ":"Ꮙ","ꮚ":"Ꮚ","ꮛ":"Ꮛ","ꮜ":"Ꮜ","ꮝ":"Ꮝ","ꮞ":"Ꮞ","ꮟ":"Ꮟ","ꮠ":"Ꮠ","ꮡ":"Ꮡ","ꮢ":"Ꮢ","ꮣ":"Ꮣ","ꮤ":"Ꮤ","ꮥ":"Ꮥ","ꮦ":"Ꮦ","ꮧ":"Ꮧ","ꮨ":"Ꮨ","ꮩ":"Ꮩ","ꮪ":"Ꮪ","ꮫ":"Ꮫ","ꮬ":"Ꮬ","ꮭ":"Ꮭ","ꮮ":"Ꮮ","ꮯ":"Ꮯ","ꮰ":"Ꮰ","ꮱ":"Ꮱ","ꮲ":"Ꮲ","ꮳ":"Ꮳ","ꮴ":"Ꮴ","ꮵ":"Ꮵ","ꮶ":"Ꮶ","ꮷ":"Ꮷ","ꮸ":"Ꮸ","ꮹ":"Ꮹ","ꮺ":"Ꮺ","ꮻ":"Ꮻ","ꮼ":"Ꮼ","ꮽ":"Ꮽ","ꮾ":"Ꮾ","ꮿ":"Ꮿ","ﬀ":"ff","ﬁ":"fi","ﬂ":"fl","ﬃ":"ffi","ﬄ":"ffl","ﬅ":"st","ﬆ":"st","ﬓ":"մն","ﬔ":"մե","ﬕ":"մի","ﬖ":"վն","ﬗ":"մխ"};
  const fold = value => Array.from(value,char => CASE_FOLD[char] || char.toLowerCase()).join("");
  const integer = value => Number.isSafeInteger(value) && value >= 0;
  const code = value => value === null || (typeof value === "string" && /^[a-z][a-z0-9_]{0,79}$/.test(value));
  const fail = message => { throw new Error(message); };
  function date(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const parsed = new Date(`${value}T00:00:00Z`);
    return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0,10) === value;
  }
  function instant(value) {
    if (typeof value !== "string") return false;
    const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$/.exec(value);
    return !!match && date(match[1]) && +match[2] < 24 && +match[3] < 60 && +match[4] < 60 && (match[5] === "Z" || (+match[5].slice(1,3) < 24 && +match[5].slice(4) < 60)) && !Number.isNaN(Date.parse(value));
  }
  const nullableInstant = value => value === null || instant(value);
  function issueURL(value, number = null) {
    if (typeof value !== "string") return null;
    const match = /^https:\/\/github\.com\/iesilder\/kw-plan-weekly-trend\/issues\/([1-9]\d{0,9})$/.exec(value);
    if (!match || !Number.isSafeInteger(Number(match[1])) || (number !== null && Number(match[1]) !== number)) return null;
    return value;
  }
  function registrationURL() { return `${REPO}/issues/new?template=executive-interest.yml`; }
  function collectionURL() { return `${REPO}/issues/new?template=interest-collection.yml`; }
  function managementURL() { return `${REPO}/issues?q=${encodeURIComponent('is:issue "[관심사]" in:title')}`; }
  function words(value, minimum = 0) {
    return Array.isArray(value) && value.length >= minimum && value.length <= 10 && value.every(word => text(word,80)) && new Set(value.map(word => trim(fold(word)))).size === value.length;
  }
  function keywordMatches(title, keyword) {
    if (typeof title !== "string" || typeof keyword !== "string" || !keyword.trim()) return false;
    // ASCII tokens use the same letter/number boundaries as the collector.
    if (/^[\x00-\x7f]+$/.test(keyword)) {
      const escaped = fold(keyword).replace(/[.*+?^${}()|[\]\\]/g,"\\$&");
      return new RegExp(`(^|[^a-z0-9])${escaped}(?=$|[^a-z0-9])`).test(fold(title));
    }
    return fold(title.replace(SPACE,"")).includes(fold(keyword.replace(SPACE,"")));
  }
  function matchingKeywords(title, interest) {
    if (interest.exclude_keywords.some(word => keywordMatches(title,word))) return [];
    return interest.keywords.filter(word => keywordMatches(title,word));
  }
  function allowedArticle(item) {
    return plain(item) && item.is_demo === false && item.content_filter === "topics" && NEWS.has(item.source_name) && typeof item.title === "string" && !["content_text","body","article_body"].some(key => item[key] !== undefined && item[key] !== null && item[key] !== "") && (!Array.isArray(item.evidence) || item.evidence.every(row => !String(row?.source_name || "").startsWith("DART")));
  }
  function validateSidecar(value, latest) {
    if (!plain(value) || value.schema_version !== "1.0" || !plain(latest) || latest.is_demo !== false || !Array.isArray(latest.candidates) || value.generation_id !== latest.generation_id || !text(value.generation_id,100) || !instant(value.generated_at)) fail("관심사 자료와 현재 수집 자료의 버전을 확인할 수 없습니다.");
    if (!plain(value.registry) || !["never_synced","success","failed","blocked"].includes(value.registry.status) || !nullableInstant(value.registry.last_attempt_at) || !nullableInstant(value.registry.last_success_at) || !code(value.registry.error_code) || (value.registry.last_attempt_at && value.registry.last_success_at && Date.parse(value.registry.last_success_at) > Date.parse(value.registry.last_attempt_at))) fail("관심사 등록부의 동기화 상태를 확인할 수 없습니다.");
    if (!Array.isArray(value.interests) || value.interests.length > 1000) fail("관심사 목록의 형식이 올바르지 않습니다.");
    const candidates = new Map(latest.candidates.map(item => [item.issue_id,item]));
    if (candidates.size !== latest.candidates.length) fail("현재 자료 ID가 중복되어 관심사를 연결할 수 없습니다.");
    const today = new Date(Date.parse(value.generated_at) + 9 * 60 * 60 * 1000).toISOString().slice(0,10);
    const seen = new Set();
    const interests = value.interests.map(item => {
      if (!plain(item) || !Number.isSafeInteger(item.issue_number) || item.issue_number <= 0 || item.issue_number > 9999999999 || item.id !== `interest-${item.issue_number}` || seen.has(item.id) || !text(item.title,120) || !text(item.question,1000) || !words(item.keywords,1) || !words(item.exclude_keywords) || !TOPICS.has(item.topic_id) || !date(item.active_from) || !(item.active_until === null || (date(item.active_until) && item.active_until >= item.active_from)) || !instant(item.updated_at) || !issueURL(item.source_url,item.issue_number) || typeof item.enabled !== "boolean" || !["active","expired","disabled"].includes(item.state) || !nullableInstant(item.last_collected_at) || !COLLECTION.has(item.collection_status) || !integer(item.match_count) || !Array.isArray(item.matches) || item.matches.length > candidates.size || item.match_count !== item.matches.length) fail("개별 관심사의 등록 정보나 관련 기사 형식이 올바르지 않습니다.");
      seen.add(item.id);
      const expectedState = !item.enabled || item.active_from > today ? "disabled" : item.active_until !== null && item.active_until < today ? "expired" : "active";
      if (item.state !== expectedState || (item.state !== "active" && item.matches.length)) fail("관심사의 유효 기간과 관련 기사 상태가 일치하지 않습니다.");
      const matchedIds = new Set();
      const matches = item.matches.map(match => {
        const candidate = candidates.get(match?.issue_id);
        if (!plain(match) || !/^issue-[a-f0-9]{20}$/.test(match.issue_id) || matchedIds.has(match.issue_id) || !allowedArticle(candidate) || !words(match.keywords,1) || !match.keywords.every(word => item.keywords.includes(word))) fail("관심사와 연결된 기사의 출처나 키워드를 확인할 수 없습니다.");
        const expected = matchingKeywords(candidate.title,item);
        if (expected.length !== match.keywords.length || !expected.every(word => match.keywords.includes(word))) fail("관심사 관련 기사가 제목 키워드 조건과 일치하지 않습니다.");
        matchedIds.add(match.issue_id);
        return {issue_id:match.issue_id,keywords:[...match.keywords]};
      });
      return {id:item.id,issue_number:item.issue_number,title:item.title,question:item.question,keywords:[...item.keywords],exclude_keywords:[...item.exclude_keywords],topic_id:item.topic_id,active_from:item.active_from,active_until:item.active_until,updated_at:item.updated_at,source_url:item.source_url,enabled:item.enabled,state:item.state,last_collected_at:item.last_collected_at,collection_status:item.collection_status,match_count:item.match_count,matches};
    });
    if (interests.filter(item => item.state === "active").length > 20) fail("활성 관심사 수가 허용 범위를 넘었습니다.");
    let lastCollection = null;
    if (value.last_collection !== null) {
      const run = value.last_collection;
      if (!plain(run) || !["daily","manual"].includes(run.mode) || !instant(run.started_at) || !instant(run.completed_at) || Date.parse(run.completed_at) < Date.parse(run.started_at) || !["success","partial_success","failed","skipped"].includes(run.status) || !(run.request_url === null || issueURL(run.request_url)) || !code(run.error_code)) fail("관심사 수집 요청의 실행 상태를 확인할 수 없습니다.");
      lastCollection = {mode:run.mode,started_at:run.started_at,completed_at:run.completed_at,status:run.status,request_url:run.request_url,error_code:run.error_code};
    }
    return {schema_version:"1.0",generation_id:value.generation_id,generated_at:value.generated_at,registry:{status:value.registry.status,last_attempt_at:value.registry.last_attempt_at,last_success_at:value.registry.last_success_at,error_code:value.registry.error_code},last_collection:lastCollection,interests};
  }
  function normalizeFilter(data, id) { return data?.interests.some(item => item.id === id && item.state === "active") ? id : ""; }
  function matchingInterests(data, issueId) { return data ? data.interests.filter(item => item.state === "active" && item.matches.some(match => match.issue_id === issueId)) : []; }
  function filterCandidates(data, candidates, id) {
    if (!id) return [...candidates];
    const interest = data?.interests.find(item => item.id === id && item.state === "active");
    if (!interest) return [];
    const ids = new Set(interest.matches.map(match => match.issue_id));
    return candidates.filter(item => ids.has(item.issue_id));
  }
  function summary(data) {
    const active = data.interests.filter(item => item.state === "active").length;
    let label = active ? `활성 ${active}건` : "활성 관심사 없음";
    let note = active ? "등록된 관심사별 제목 키워드와 일치한 기사를 연결합니다. 의미 기반 AI 판정은 아닙니다." : "관심사를 등록하면 다음 매일 11:00(한국 시간) 정기 수집부터 반영됩니다.";
    if (data.registry.status === "never_synced") { label = "등록부 확인 전"; note = "아직 GitHub 관심사 등록부를 동기화하지 않았습니다. 미등록이나 관련 기사 0건으로 판단하지 않습니다."; }
    else if (["failed","blocked"].includes(data.registry.status)) { label = "등록부 확인 필요"; note = "최근 GitHub 관심사 등록부를 확인하지 못했습니다. 표시된 목록은 마지막 저장 정보이며, 새 등록·변경이 반영되지 않았을 수 있습니다."; }
    return {label,note,activeCount:active,canRequest:true};
  }
  return {validateSidecar,issueURL,registrationURL,collectionURL,managementURL,keywordMatches,matchingKeywords,normalizeFilter,matchingInterests,filterCandidates,summary,statusLabels:STATUS_LABELS};
});
