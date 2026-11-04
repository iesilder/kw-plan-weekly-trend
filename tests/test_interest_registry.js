"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const Registry = require("../site/interest_registry.js");

const REPO = "https://github.com/iesilder/kw-plan-weekly-trend";
const API = "https://api.github.com/repos/iesilder/kw-plan-weekly-trend/issues";
const NOW = "2026-11-05T01:00:00.000Z";
const CREATED = "2026-11-01T00:00:00Z";
const A = "issue-" + "a".repeat(20), B = "issue-" + "b".repeat(20);
const CONSENT = "관심사와 검색어가 공개 GitHub 이슈와 웹사이트에 저장됨을 이해합니다.";
const HEADINGS = ["관심 주제", "보고 질문", "검색어", "제외 검색어", "분류", "시작일", "종료일", "공개 저장 동의"];
const FIELDS = ["id", "issue_number", "title", "question", "keywords", "exclude_keywords", "topic_id", "active_from", "active_until", "updated_at", "source_url", "enabled"];
const clone = value => JSON.parse(JSON.stringify(value));

function form(changes = {}) {
  const values = {"관심 주제":"가상 증권사 AI 업무 적용", "보고 질문":"경쟁사의 AI 전환은\n어디까지 진행됐는가?", "검색어":"AI, AI서비스\nAX, AI전환", "제외 검색어":"채용", "분류":"신사업·상품·고객전략", "시작일":"2026-11-01", "종료일":"_No response_", "공개 저장 동의":"- [X] " + CONSENT, ...changes};
  return HEADINGS.map(heading => `### ${heading}\n\n${values[heading]}`).join("\n\n");
}
function issue(number = 10, changes = {}) {
  return {number, title:"[관심사] 관심사항 등록", body:form(), state:"open", user:{login:"iesilder"}, html_url:`${REPO}/issues/${number}`, created_at:CREATED, updated_at:CREATED, labels:[], ...changes};
}
function record(number = 10, changes = {}) {
  return {id:`interest-${number}`, issue_number:number, title:"가상 증권사 AI 업무 적용", question:"경쟁사의 AI 전환은 어디까지 진행됐는가?", keywords:["AI","AI서비스","AX","AI전환"], exclude_keywords:["채용"], topic_id:"digital", active_from:"2026-11-01", active_until:null, updated_at:CREATED, source_url:`${REPO}/issues/${number}`, enabled:true, ...changes};
}
function response(payload, {status = 200, headers = {}, raw} = {}) {
  const normalized = Object.fromEntries(Object.entries(headers).map(([key,value]) => [key.toLowerCase(),String(value)]));
  return {ok:status >= 200 && status < 300, status, headers:{get:name => normalized[name.toLowerCase()] ?? null}, text:async () => raw === undefined ? JSON.stringify(payload) : raw};
}
function client(fetchImpl, initial = NOW) {
  let milliseconds = Date.parse(initial);
  return {value:Registry.createClient({fetchImpl,now:() => new Date(milliseconds),minimumIntervalMs:60000}), advance:delta => { milliseconds += delta; }};
}
function safeError(error, code, number = null) {
  assert.ok(error instanceof Error);
  assert.equal(error.code,code);
  assert.match(error.message,/[가-힣]/);
  assert.doesNotMatch(error.message,/SECRET|RAW_RESPONSE|synthetic-token|<script>/);
  if (number !== null) { assert.equal(error.issue_number,number); assert.ok(error.message.includes(`#${number}`)); }
  return true;
}
function fixture() {
  const current = record();
  const latest = {schema_version:"1.0",generation_id:"live-registry-tests",is_demo:false,candidates:[{issue_id:A,title:"증권사 AI 서비스 전환",source_name:"연합인포맥스(기사) · 증권",content_filter:"topics",is_demo:false,published_date:"2026-11-05"}]};
  const run = {mode:"manual",started_at:CREATED,completed_at:CREATED,status:"success",request_url:`${REPO}/issues/11`,error_code:null};
  const published = {schema_version:"1.0",generation_id:latest.generation_id,generated_at:NOW,registry:{status:"success",last_attempt_at:CREATED,last_success_at:CREATED,error_code:null},last_collection:run,interests:[{...current,state:"active",last_collected_at:CREATED,collection_status:"success",match_count:1,matches:[{issue_id:A,keywords:["AI","AI서비스"]}]}]};
  return {published,latest,live:{fetched_at:NOW,interests:[current]}};
}

test("owner form becomes the exact bounded public record without retaining Issue payload",async () => {
  const calls = [];
  const {value} = client(async (url,options) => { calls.push({url,options}); return response([issue()]); });
  const loaded = await value.load();
  assert.deepEqual(loaded,{fetched_at:NOW,interests:[record()]});
  assert.deepEqual(Object.keys(loaded.interests[0]).sort(),FIELDS.slice().sort());
  const url = new URL(calls[0].url);
  assert.equal(url.origin + url.pathname,API);
  assert.equal(url.searchParams.get("state"),"open");
  assert.equal(url.searchParams.get("creator"),"iesilder");
  assert.equal(url.searchParams.get("per_page"),"100");
  assert.equal(url.searchParams.get("page"),"1");
  assert.equal(calls.length,1);
  assert.ok(!calls[0].options?.body);
  assert.ok(!calls[0].options?.method || calls[0].options.method === "GET");
  assert.doesNotMatch(JSON.stringify(loaded),/"body"|"labels"|"user"/);
});

test("foreign authors, PR pull URLs and manual collection requests never become registrations",async () => {
  const payload = [issue(),issue(11,{user:{login:"another-user"},body:"SECRET unauthorized malformed body"}),issue(12,{pull_request:{url:"unused"},html_url:`${REPO}/pull/12`,body:null}),issue(13,{title:"[관심사 수집 요청]",body:null}),issue(14,{title:"ordinary Issue",body:null})];
  const {value} = client(async () => response(payload));
  assert.deepEqual((await value.load()).interests,[record()]);
});

test("all Korean dropdown labels and legacy topic IDs normalize to the five topic IDs",async () => {
  const topics = {performance:"실적·자본·주주정책",digital:"신사업·상품·고객전략",expansion:"제휴·인수·해외사업",strategy:"조직·경영전략",risk:"정책·인허가·리스크"};
  for (const [id,label] of Object.entries(topics)) for (const selected of [id,label]) {
    const {value} = client(async () => response([issue(10,{title:"[관심사]",body:form({"분류":selected})})]));
    assert.equal((await value.load()).interests[0].topic_id,id);
  }
});

test("blank dates use the Issue creation Korean day, independently of fetch day",async () => {
  const {value} = client(async () => response([issue(10,{created_at:"2026-11-01T15:00:00Z",body:form({"시작일":"_No response_","종료일":""})})]));
  const loaded = await value.load();
  assert.equal(loaded.interests[0].active_from,"2026-11-02");
  assert.equal(loaded.interests[0].active_until,null);
  assert.equal(loaded.fetched_at,NOW);
  assert.deepEqual(loaded.blank_start_ids,["interest-10"]);
});

test("blank start metadata retains an already applied explicit start after form clearing",async () => {
  const {value} = client(async () => response([issue(10,{created_at:"2026-11-04T00:00:00Z",body:form({"시작일":"_No response_"})})]));
  const live = await value.load(), {published,latest} = fixture();
  assert.equal(live.interests[0].active_from,"2026-11-04");
  const merged = Registry.mergeRegistry(published,live,latest).interests[0];
  assert.equal(merged.active_from,"2026-11-01");
  assert.equal(merged.registration_status,"applied");
  assert.equal(merged.collection_status,"success");
  assert.deepEqual(merged.matches,published.interests[0].matches);
});

test("malformed owner headings and absent consent fail with safe issue-number diagnostics",async () => {
  const bodies = [form().replace("### 검색어","### 검색어\n\nAI\n\n### 검색어"),form() + "\n\n### SECRET unknown\n\nRAW_RESPONSE",form({"공개 저장 동의":"- [ ] " + CONSENT}),form().replace("### 보고 질문","### SECRET report"),null];
  for (const body of bodies) {
    const {value} = client(async () => response([issue(10,{body})]));
    await assert.rejects(value.load(),error => safeError(error,"invalid_registration",10));
  }
});

test("owner forms reject invalid bounds, duplicate words, dates and source identities atomically",async () => {
  const bad = [issue(10,{body:form({"관심 주제":"x".repeat(121)})}),issue(10,{body:form({"보고 질문":"x".repeat(1001)})}),issue(10,{body:form({"검색어":"_No response_"})}),issue(10,{body:form({"검색어":Array.from({length:11},(_,i) => `term${i}`).join(",")})}),issue(10,{body:form({"검색어":"AI, ai"})}),issue(10,{body:form({"제외 검색어":"x".repeat(81)})}),issue(10,{body:form({"분류":"all"})}),issue(10,{body:form({"시작일":"2026-02-30"})}),issue(10,{body:form({"종료일":"2026-10-31"})}),issue(10,{html_url:"https://github.com/evil/repo/issues/10"}),issue(10,{updated_at:"yesterday"})];
  for (const badIssue of bad) {
    const {value} = client(async () => response([issue(9),badIssue]));
    await assert.rejects(value.load(),error => safeError(error,"invalid_registration",10));
  }
});

test("active cap counts overlapping intervals, while historical nonoverlapping records remain readable",async () => {
  const concurrent = Array.from({length:21},(_,i) => issue(i+1));
  const tooMany = client(async () => response(concurrent));
  await assert.rejects(tooMany.value.load(),error => safeError(error,"active_limit"));
  const historical = concurrent.map((item,index) => index < 20 ? {...item,body:form({"시작일":"2026-01-01","종료일":"2026-01-02"})} : item);
  const allowed = client(async () => response(historical));
  assert.equal((await allowed.value.load()).interests.length,21);
});

test("a full first page is followed to the next page before returning the registry",async () => {
  const urls = [];
  const {value} = client(async url => { urls.push(url); return response(urls.length === 1 ? Array.from({length:100},(_,i) => issue(i+100,{title:"unrelated",body:null})) : [issue()]); });
  assert.deepEqual((await value.load()).interests,[record()]);
  assert.deepEqual(urls.map(url => new URL(url).searchParams.get("page")),["1","2"]);
  assert.ok(urls.every(url => new URL(url).origin + new URL(url).pathname === API));
});

test("duplicate page identities and an unfinished fifth full page cannot yield partial registries",async () => {
  let page = 0;
  const duplicate = client(async () => response(++page === 1 ? Array.from({length:100},(_,i) => issue(i+1,{title:"unrelated",body:null})) : [issue(1)]));
  await assert.rejects(duplicate.value.load(),error => safeError(error,"invalid_response"));
  page = 0;
  const bounded = client(async () => { const offset = page++ * 100; return response(Array.from({length:100},(_,i) => issue(offset+i+1,{title:"unrelated",body:null}))); });
  await assert.rejects(bounded.value.load(),error => safeError(error,"pagination_limit"));
  assert.equal(page,5);
});

test("oversized text and invalid JSON fail safely without exposing raw HTTP contents",async () => {
  for (const [reply,code] of [[response(null,{raw:"x".repeat(4_000_000)}),"response_too_large"],[response(null,{raw:"SECRET RAW_RESPONSE"}),"invalid_response"],[response({message:"SECRET RAW_RESPONSE"}),"invalid_response"]]) {
    const {value} = client(async () => reply);
    await assert.rejects(value.load(),error => safeError(error,code));
  }
});

test("streaming responses enforce the same byte limit before parsing or returning a partial page",async () => {
  let cancelled = false;
  const body = new ReadableStream({start(controller) { controller.enqueue(new Uint8Array(4_000_000)); },cancel() { cancelled = true; }});
  const {value} = client(async () => ({ok:true,status:200,headers:{get:() => null},body,text:async () => { throw Error("must not read text after oversized stream"); }}));
  await assert.rejects(value.load(),error => safeError(error,"response_too_large"));
  assert.equal(cancelled,true);
});

test("ordinary loads share one request and use the cache for a minute without mutating cached results",async () => {
  let calls = 0,release;
  const pending = new Promise(resolve => { release = resolve; });
  const clock = client(async () => { calls++; await pending; return response([issue()]); });
  const first = clock.value.load(),second = clock.value.load();
  release();
  const [one,two] = await Promise.all([first,second]);
  assert.equal(calls,1);
  assert.deepEqual(one,two);
  one.interests[0].keywords.push("caller mutation");
  clock.advance(59000);
  assert.deepEqual((await clock.value.load()).interests,[record()]);
  assert.equal(calls,1);
  clock.advance(1001);
  await clock.value.load();
  assert.equal(calls,2);
});

test("force sees a registration created five seconds after an empty load and concurrent force deduplicates",async () => {
  let calls = 0,release;
  const pending = new Promise(resolve => { release = resolve; });
  const clock = client(async () => { calls++; if (calls === 1) return response([]); await pending; return response([issue()]); });
  assert.deepEqual((await clock.value.load()).interests,[]);
  clock.advance(5000);
  const first = clock.value.load({force:true}),second = clock.value.load({force:true});
  release();
  const results = await Promise.all([first,second]);
  assert.equal(calls,2);
  for (const loaded of results) assert.deepEqual(loaded.interests,[record()]);
});

test("429 Retry-After blocks ordinary and forced calls until the server deadline without automatic retry",async () => {
  let calls = 0;
  const clock = client(async () => ++calls === 1 ? response({message:"SECRET RAW_RESPONSE"},{status:429,headers:{"Retry-After":"120"}}) : response([issue()]));
  const rejects = () => assert.rejects(clock.value.load({force:true}),error => { safeError(error,"rate_limited"); assert.ok(Date.parse(error.retry_at) >= Date.parse(NOW)+120000); return true; });
  await rejects();
  clock.advance(61000);
  await rejects();
  assert.equal(calls,1);
  clock.advance(60000);
  assert.deepEqual((await clock.value.load({force:true})).interests,[record()]);
  assert.equal(calls,2);
});

test("403 rate-limit reset backoff also applies to force and unexpected fetch failures stay safe",async () => {
  let calls = 0;
  const deadline = Date.parse(NOW)+180000;
  const clock = client(async () => { calls++; return response({message:"SECRET"},{status:403,headers:{"X-RateLimit-Remaining":"0","X-RateLimit-Reset":String(deadline/1000)}}); });
  for (const step of [0,61000]) { clock.advance(step); await assert.rejects(clock.value.load({force:true}),error => { safeError(error,"rate_limited"); assert.ok(Date.parse(error.retry_at) >= deadline); return true; }); }
  assert.equal(calls,1);
  const failed = client(async () => { throw Error("SECRET synthetic-token RAW_RESPONSE"); });
  await assert.rejects(failed.value.load(),error => safeError(error,"network_error"));
  const namedFailure = client(async () => { throw Object.assign(Error("SECRET RAW_RESPONSE"),{code:"network_error"}); });
  await assert.rejects(namedFailure.value.load(),error => safeError(error,"network_error"));
  const longBackoff = client(async () => response(null,{status:429,headers:{"Retry-After":String(10*86400)}}));
  await assert.rejects(longBackoff.value.load(),error => { safeError(error,"rate_limited"); assert.equal(Date.parse(error.retry_at),Date.parse(NOW)+10*86400000); return true; });
});

test("new live registrations show pending cards while retaining the actual collection run",() => {
  const {published,latest,live} = fixture();
  live.interests.push(record(20,{title:"신규 관심사"}));
  const before = clone({published,latest,live});
  const merged = Registry.mergeRegistry(published,live,latest);
  assert.equal(merged.registration_live,true);
  assert.equal(merged.registry_fetched_at,NOW);
  assert.equal(merged.registry_view_status,"live");
  assert.deepEqual(merged.last_collection,published.last_collection);
  const added = merged.interests.find(item => item.id === "interest-20");
  assert.equal(added.registration_status,"pending");
  assert.equal(added.collection_status,"pending");
  assert.equal(added.last_collected_at,null);
  assert.equal(added.match_count,0);
  assert.deepEqual(added.matches,[]);
  assert.deepEqual({published,latest,live},before);
});

test("timestamp-only edits reuse applied results and older live caches cannot overwrite newer publication",() => {
  const {published,latest,live} = fixture();
  live.interests[0].updated_at = "2026-11-05T00:59:00Z";
  const value = Registry.mergeRegistry(published,live,latest).interests[0];
  assert.equal(value.registration_status,"applied");
  assert.equal(value.collection_status,"success");
  assert.equal(value.last_collected_at,CREATED);
  assert.deepEqual(value.matches,published.interests[0].matches);
  value.matches[0].keywords.push("caller mutation");
  assert.deepEqual(published.interests[0].matches,[{issue_id:A,keywords:["AI","AI서비스"]}]);
  const newer = fixture();
  newer.published.registry.last_success_at = "2026-11-05T01:01:00Z";
  newer.published.registry.last_attempt_at = newer.published.registry.last_success_at;
  newer.published.interests[0].title = "이미 게시된 새 관심사 제목";
  newer.published.interests.push({...record(20),state:"active",last_collected_at:null,collection_status:"never_collected",match_count:0,matches:[]});
  const stale = Registry.mergeRegistry(newer.published,newer.live,newer.latest);
  assert.equal(stale.registration_live,false);
  assert.equal(stale.registry_view_status,"stale");
  assert.equal(stale.registry_fetched_at,NOW);
  assert.equal(stale.interests.length,2);
  assert.equal(stale.interests.find(item => item.id === "interest-10").title,"이미 게시된 새 관심사 제목");
  assert.ok(stale.interests.every(item => item.registration_status === "applied"));
  const fresh = Registry.mergeRegistry(newer.published,{fetched_at:"2026-11-05T01:02:00Z",interests:[]},newer.latest);
  assert.ok(fresh.interests.every(item => item.registration_status === "inactive" && item.state === "disabled" && !item.matches.length));
});

test("every matching or reporting edit becomes changed and drops earlier collection claims",() => {
  const edits = [{title:"새 주제"},{question:"새 보고 질문"},{keywords:["AX"]},{exclude_keywords:[]},{topic_id:"strategy"},{active_from:"2026-11-02"},{active_until:"2026-11-06"}];
  for (const edit of edits) {
    const {published,latest,live} = fixture();
    Object.assign(live.interests[0],edit);
    const value = Registry.mergeRegistry(published,live,latest).interests[0];
    assert.equal(value.registration_status,"changed");
    assert.equal(value.collection_status,"stale");
    assert.equal(value.last_collected_at,null);
    assert.equal(value.match_count,0);
    assert.deepEqual(value.matches,[]);
  }
});

test("closed, expired and future registrations remain inactive with no prior matches",() => {
  for (const mode of ["closed","expired","future"]) {
    const {published,latest,live} = fixture();
    if (mode === "closed") live.interests=[];
    if (mode === "expired") live.interests[0].active_until="2026-11-04";
    if (mode === "future") live.interests[0].active_from="2026-11-06";
    const value = Registry.mergeRegistry(published,live,latest).interests[0];
    assert.equal(value.registration_status,"inactive");
    assert.equal(value.state,mode === "expired" ? "expired" : "disabled");
    assert.equal(value.match_count,0);
    assert.deepEqual(value.matches,[]);
  }
});

test("a previously future registration becomes pending until a real active batch is published",() => {
  const {published,latest,live} = fixture();
  published.generated_at = "2026-11-04T01:00:00Z";
  published.interests[0].active_from = "2026-11-05";
  published.interests[0].state = "disabled";
  published.interests[0].collection_status = "disabled";
  published.interests[0].match_count = 0;
  published.interests[0].matches = [];
  live.interests[0].active_from = "2026-11-05";
  const active = Registry.mergeRegistry(published,live,latest).interests[0];
  assert.equal(active.state,"active");
  assert.equal(active.registration_status,"pending");
  assert.equal(active.collection_status,"pending");
  assert.equal(active.last_collected_at,null);
  assert.deepEqual(active.matches,[]);
});

test("missing or mismatched publication and invalid current article IDs preserve registration cards only",() => {
  const {published,latest,live} = fixture();
  for (const [saved,current] of [[null,null],[published,null],[{...published,generation_id:"older-generation"},latest],[published,{...latest,candidates:[]}]]) {
    const merged = Registry.mergeRegistry(saved,live,current);
    assert.equal(merged.interests.length,1);
    assert.equal(merged.interests[0].title,record().title);
    assert.equal(merged.interests[0].last_collected_at,null);
    assert.equal(merged.interests[0].match_count,0);
    assert.deepEqual(merged.interests[0].matches,[]);
    assert.deepEqual(merged.last_collection,saved?.last_collection ?? null);
  }
  const invalid = clone(published);
  invalid.interests[0].matches=[{issue_id:B,keywords:["AI"]}];
  const value = Registry.mergeRegistry(invalid,live,latest).interests[0];
  assert.equal(value.last_collected_at,null);
  assert.deepEqual(value.matches,[]);
});
