"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const AI = require("../site/ai.js");
const Article = require("../site/article.js");
const copy = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));
const paths = ["data/latest.json","data/status.json","data/ai-analysis.json","data/article-analysis.json"];
function fixture() {
  return Object.fromEntries(paths.map(path => [path,JSON.parse(fs.readFileSync(require.resolve(`../site/${path}`),"utf8"))]));
}
function deferred() { let resolve; const promise = new Promise(yes => { resolve = yes; }); return {promise,resolve}; }
function harness({data = fixture(),fetchImpl,missingPage = false} = {}) {
  const all = [],ids = new Map(),requests = [],storage = [];
  class Node {
    constructor(tag = "div") { this.tagName = tag.toUpperCase();this.children = [];this.attrs = {};this.events = {};this.hidden = false;this.disabled = false;this.className = "";all.push(this); }
    set textContent(value) { this.text = String(value);this.children = []; }
    get textContent() { return (this.text || "") + this.children.map(node => node.textContent).join(""); }
    set innerHTML(_) { throw Error("Operational data must be inert text"); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.text = "";this.children = [...nodes]; }
    setAttribute(key,value) { this.attrs[key] = String(value); }
    addEventListener(type,listener) { (this.events[type] ||= []).push(listener); }
    fire(type) { for (const listener of this.events[type] || []) listener({target:this}); }
    showModal() { this.open = true; } close() { this.open = false; }
  }
  const html = fs.readFileSync(require.resolve("../site/admin.html"),"utf8");
  for (const match of html.matchAll(/\bid="([^"]+)"/g)) ids.set(match[1],new Node());
  if (missingPage) ids.delete("admin-reload");
  ids.get("batch-today-state").textContent = "BATCH_INDEPENDENT";
  const document = {getElementById:id => ids.get(id) || null,createElement:tag => new Node(tag),createTextNode:text => { const node = new Node("#text");node.textContent = text;return node; }};
  const response = value => ({ok:true,status:200,text:async () => JSON.stringify(copy(value))});
  const fetch = async (path,options) => {
    requests.push({path,options});
    if (fetchImpl) return fetchImpl(path,options,response,requests.length);
    if (!Object.hasOwn(data,path)) throw Error("Unexpected operation");
    return response(data[path]);
  };
  const localStorage = {getItem(...args) { storage.push(args);throw Error("No main choices should be accessed"); },setItem(...args) { storage.push(args);throw Error("No main choices should be changed"); }};
  const window = {WeeklyAI:AI,WeeklyArticle:Article};
  const context = vm.createContext({window,document,fetch,localStorage,Intl,Date,AbortController,TextEncoder,setTimeout,clearTimeout});
  vm.runInContext(fs.readFileSync(require.resolve("../site/admin.js"),"utf8"),context);
  return {$:id => ids.get(id),all,requests,storage,settle:tick,reload:async () => { ids.get("admin-reload").fire("click");await tick(); }};
}

test("management page renders validated saved collection, title AI and body counts without running work",async () => {
  const data = fixture(),before = JSON.stringify(data),h = harness({data});await h.settle();
  assert.match(h.$("issue-count").textContent,new RegExp(`^${data[paths[0]].candidates.length}건$`));
  assert.equal(h.$("ai-readiness").textContent,"AI 제안 확인 가능");
  assert.equal(h.$("article-readiness").textContent,"일부 분석 실패");
  assert.match(h.$("ai-counts").textContent,/현재 결과6건/);
  assert.match(h.$("article-counts").textContent,/현재 요약5건/);
  assert.match(h.$("ai-warning").textContent,/DART 공시는 AI 입력에서 제외/);
  assert.match(h.$("article-warning").textContent,/끝나지 않은 자료/);
  assert.equal(h.$("admin-error").hidden,true);
  assert.equal(h.$("admin-reload").disabled,false);
  assert.equal(h.$("batch-today-state").textContent,"BATCH_INDEPENDENT");
  assert.deepEqual(h.requests.map(request => request.path),paths);
  for (const {options} of h.requests) { assert.equal(options.method,"GET");assert.equal(options.credentials,"omit");assert.equal(options.cache,"no-store");assert.equal(options.body,undefined); }
  assert.deepEqual(h.storage,[]);assert.equal(JSON.stringify(data),before);
});

test("a missing title sidecar leaves article processing and saved collection available without exposing error text",async () => {
  const data = fixture(),h = harness({fetchImpl:async (path,options,response) => {
    if (path === paths[2]) throw Error("SECRET raw provider response token=123");
    return response(data[path]);
  }});await h.settle();
  assert.equal(h.$("ai-readiness").textContent,"상태 확인 불가");
  assert.equal(h.$("ai-counts").textContent,"");
  assert.equal(h.$("article-readiness").textContent,"일부 분석 실패");
  assert.match(h.$("article-counts").textContent,/현재 요약5건/);
  assert.equal(h.$("admin-error").hidden,true);
  assert.doesNotMatch(h.all.map(node => node.textContent).join("\n"),/SECRET|token=|provider response/);
});

test("generation or evidence mismatch never displays a body sidecar as current while title counts survive",async () => {
  for (const change of [data => { data[paths[3]].generation_id = "old-generation"; },data => { const item = Object.values(data[paths[3]].items)[0];item.evidence_ids = ["version-"+"0".repeat(20)]; }]) {
    const data = fixture();change(data);const h = harness({data});await h.settle();
    assert.equal(h.$("article-readiness").textContent,"상태 확인 불가");
    assert.equal(h.$("article-counts").textContent,"");
    assert.match(h.$("article-warning").textContent,/버전·근거·건수/);
    assert.equal(h.$("ai-readiness").textContent,"AI 제안 확인 가능");
  }
});

test("failed primary reload clears old success and counts while batch history remains independent",async () => {
  const data = fixture();let fail = false;
  const h = harness({fetchImpl:async (path,options,response) => {
    if (fail && path === paths[0]) throw Error("SECRET raw network error");return response(data[path]);
  }});await h.settle();assert.match(h.$("ai-counts").textContent,/현재 결과6건/);
  fail = true;await h.reload();
  assert.equal(h.$("last-run").textContent,"확인 불가");
  assert.equal(h.$("issue-count").textContent,"—건");
  assert.equal(h.$("ai-counts").textContent,"");assert.equal(h.$("article-counts").textContent,"");
  assert.equal(h.$("source-status-list").textContent,"");
  assert.equal(h.$("admin-error").hidden,false);
  assert.match(h.$("admin-error").textContent,/배치 실행 기록은 독립적으로/);
  assert.equal(h.$("batch-today-state").textContent,"BATCH_INDEPENDENT");
  assert.doesNotMatch(h.all.map(node => node.textContent).join("\n"),/SECRET|raw network/);
});

test("invalid primary generations, forged counts and impossible dates fail without claiming empty success",async () => {
  for (const change of [data => { data[paths[1]].published_generation_id = "old"; },data => { data[paths[1]].issue_count++; },data => { data[paths[1]].sources[0].last_attempt_at = "2026-02-30T00:00:00Z"; }]) {
    const data = fixture();change(data);const h = harness({data});await h.settle();
    assert.equal(h.$("admin-error").hidden,false);
    assert.equal(h.$("issue-count").textContent,"—건");
    assert.equal(h.$("ai-counts").textContent,"");assert.equal(h.$("article-counts").textContent,"");
  }
});

test("retained all-source failure shows current attempts and prior success dates rather than refreshed content",async () => {
  const data = fixture(),status = data[paths[1]];
  Object.assign(status,{collection_ok:false,collection_status:"failed",publication_status:"retained",last_run_at:"2026-11-04T04:00:00Z",last_successful_run_at:"2026-11-03T03:00:00Z",interests_only:false});
  for (const source of status.sources) Object.assign(source,{status:"failed",last_attempt_at:status.last_run_at,last_success_at:status.last_successful_run_at,error_message:"SECRET source body token=abc"});
  const h = harness({data});await h.settle();
  assert.equal(h.$("source-status-panel").open,true);
  assert.match(h.$("source-status-summary").textContent,/실패 3 · 이전 자료 유지/);
  assert.match(h.$("source-status-list").textContent,/마지막으로 게시한 자료를 유지/);
  assert.match(h.$("source-status-list").textContent,/최근 시도 2026\.11\.04 · 13:00 · 최근 성공 2026\.11\.03 · 12:00/);
  assert.match(h.$("last-run").textContent,/2026\.11\.04 · 13:00/);
  assert.doesNotMatch(h.all.map(node => node.textContent).join("\n"),/SECRET|token=abc/);
});

test("empty interests and unchanged sources are described as skipped or cached rather than a new collection",async () => {
  const data = fixture();Object.assign(data[paths[1]],{interests_only:true,interests_count:0,sources:[]});
  const h = harness({data});await h.settle();
  assert.equal(h.$("source-status-summary").textContent,"활성 관심사 없어 수집 생략");
  assert.match(h.$("notice-description").textContent,/활성 관심사가 없어 기사 수집을 생략/);
  const unchanged = fixture();for (const source of unchanged[paths[1]].sources) source.status = "not_modified";
  const next = harness({data:unchanged});await next.settle();
  assert.match(next.$("source-status-summary").textContent,/304 3/);
  assert.match(next.$("source-status-list").textContent,/HTTP 304: 출처가 피드 변경 없음/);
});

test("a late previous reload cannot replace the newer generation or release its loading state",async () => {
  const original = fixture(),newer = copy(original),waiting = deferred();
  const generation = "live-admin-new";
  for (const path of paths) { newer[path].generation_id = generation;if (Object.hasOwn(newer[path],"published_generation_id")) newer[path].published_generation_id = generation; }
  newer[paths[1]].last_run_at = "2026-11-04T05:00:00Z";
  let calls = 0;
  const h = harness({fetchImpl:async (path,options,response) => {
    const ordinal = ++calls;
    if (ordinal <= 4) { await waiting.promise;return response(original[path]); }
    return response(newer[path]);
  }});
  await h.reload();assert.match(h.$("last-run").textContent,/2026\.11\.04 · 14:00/);
  waiting.resolve();await h.settle();
  assert.match(h.$("last-run").textContent,/2026\.11\.04 · 14:00/);
  assert.equal(h.$("admin-reload").disabled,false);assert.equal(h.$("admin-error").hidden,true);
});

test("source labels remain inert text and management history dialog does not mutate human choices",async () => {
  const data = fixture(),marker = '<img src=x onerror="alert(1)">';data[paths[1]].sources[0].source_name = marker;
  const h = harness({data});await h.settle();
  assert(h.$("source-status-list").textContent.includes(marker));
  assert.equal(h.all.filter(node => ["IMG","SCRIPT","SVG"].includes(node.tagName)).length,0);
  h.$("history-open").fire("click");assert.equal(h.$("history-dialog").open,true);
  h.$("history-close").fire("click");assert.equal(h.$("history-dialog").open,false);
  assert.deepEqual(h.storage,[]);
});

test("script does nothing on a page without the management reload control",async () => {
  const h = harness({missingPage:true});await h.settle();assert.equal(h.requests.length,0);
});

function rawFixture() {
  const data = fixture(),status = data[paths[1]];
  Object.assign(status,{capture_schema_version:"raw-metadata-v1",execution_mode:"collection",collection_performed:true,source_mode:"rss",interests_only:false,raw_article_count:60,raw_version_count:63,mart_article_count:status.article_count,mart_excluded_count:60-status.article_count,mart_exclusions:{outside_scope:60-status.article_count}});
  Object.assign(status.sources[0],{fetched_count:20,accepted_count:20,raw_saved_count:20,inserted_versions:3,duplicate_count:17,mart_included_count:0,mart_excluded_count:20,outside_scope_count:20});
  return data;
}

test("raw metadata totals stay distinct from dashboard counts and source filtering does not claim missing news",async () => {
  const data = rawFixture(),h = harness({data});await h.settle();
  assert.equal(h.$("admin-error").hidden,true);
  assert.equal(h.$("article-count-label").textContent,"누적 원자료");
  assert.equal(h.$("article-count").textContent,"60건");
  assert.equal(h.$("version-count").textContent,"63개");
  assert.equal(h.$("issue-count").textContent,`${data[paths[0]].candidates.length}건`);
  assert.match(h.$("source-status-list").textContent,/수신 20건 · 원자료 유효 20건 · 새 버전 3개 · 중복 17건/);
  assert.match(h.$("source-status-list").textContent,/데이터마트 반영 0건 · 데이터마트 제외 20건/);
  assert.match(h.$("source-status-list").textContent,/제외 자료도 원자료에 남아/);
  assert.match(h.$("source-status-list").textContent,/데이터마트 제외 건수는 ‘뉴스 없음’을 뜻하지 않습니다/);
  assert.doesNotMatch(h.$("source-status-list").textContent,/조건 통과 20건/);
  assert.match(h.$("notice-description").textContent,/이 수집에서는 AI를 실행하지 않습니다/);
  assert.deepEqual(h.requests.map(request => request.path),paths);
});

test("legacy snapshots disclose earlier filtered storage instead of representing it as complete raw data",async () => {
  const data = fixture(),status = data[paths[1]],h = harness({data});await h.settle();
  assert.equal(h.$("article-count-label").textContent,"보관 자료 · 이전 구조");
  assert.equal(h.$("article-count").textContent,`${status.total_article_count}건`);
  assert.match(h.$("source-status-note").textContent,/이전 구조에서 저장한 자료|마지막 관심사 별도 수집/);
});

test("offline mart rebuild reports no collection rather than unavailable source results",async () => {
  const data = rawFixture();Object.assign(data[paths[1]],{execution_mode:"mart_rebuild",collection_performed:false,sources:[]});
  const h = harness({data});await h.settle();
  assert.equal(h.$("admin-error").hidden,true);
  assert.equal(h.$("last-run-label").textContent,"마지막 데이터마트 갱신");
  assert.equal(h.$("source-status-summary").textContent,"저장 원자료 재분류 · 출처 수집 없음");
  assert.match(h.$("notice-description").textContent,/출처 수집이나 AI 분석을 실행하지 않습니다/);
  assert.doesNotMatch(h.$("source-status-list").textContent,/출처별 수집 상태가 제공되지/);
});

test("inconsistent raw totals or per-source mart results are rejected instead of displaying forged counts",async () => {
  for (const change of [data => { data[paths[1]].raw_article_count = 1; },data => { data[paths[1]].raw_version_count = 59; },data => { data[paths[1]].mart_excluded_count++; },data => { data[paths[1]].sources[0].mart_excluded_count = 19; },data => { data[paths[1]].sources[0].raw_saved_count = -1; }]) {
    const data = rawFixture();change(data);const h = harness({data});await h.settle();
    assert.equal(h.$("admin-error").hidden,false);
    assert.equal(h.$("article-count").textContent,"—건");
    assert.equal(h.$("source-status-list").textContent,"");
  }
});

test("source inventory keeps daily FSC results visible after RSS collection and offline mart rebuild",async () => {
  for (const rebuilt of [false,true]) {
    const data = rawFixture(),status = data[paths[1]];
    const fsc = {...status.sources[0],source_id:"fsc-press",source_name:"금융위원회 · 보도자료",last_attempt_at:"2026-11-04T02:00:00Z",last_success_at:"2026-11-04T02:00:00Z",raw_saved_count:12,mart_included_count:2,mart_excluded_count:10,fetched_count:12};
    status.source_inventory = [...copy(status.sources),fsc];
    if (rebuilt) Object.assign(status,{execution_mode:"mart_rebuild",collection_performed:false,sources:[]});
    const h = harness({data});await h.settle();
    assert.equal(h.$("admin-error").hidden,true);
    assert.match(h.$("source-status-list").textContent,/금융위원회 · 보도자료/);
    assert.match(h.$("source-status-list").textContent,/최근 시도 2026\.11\.04 · 11:00/);
    assert.match(h.$("source-status-note").textContent,/마지막 실행에 포함되지 않은 출처도 표시/);
    assert.match(h.$("source-status-list").textContent,/수신 12건 · 원자료 유효 12건/);
    if (!rebuilt) assert.match(h.$("source-status-summary").textContent,/출처별 최근 상태/);
  }
});

test("invalid inventory data is rejected with the same source validation as current-run data",async () => {
  const data = rawFixture();data[paths[1]].source_inventory = [{...data[paths[1]].sources[0],raw_saved_count:-1}];
  const h = harness({data});await h.settle();
  assert.equal(h.$("admin-error").hidden,false);
  assert.equal(h.$("source-status-list").textContent,"");
});
