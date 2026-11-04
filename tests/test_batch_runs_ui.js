"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const Runs = require("../site/batch_runs.js");
const NOW = "2026-11-04T03:15:00.000Z";
const tick = () => new Promise(resolve => setImmediate(resolve));
const copy = value => JSON.parse(JSON.stringify(value));

function run(workflow = "interests", extra = {}) {
  const raw = {id:41765377940,run_attempt:1,path:`.github/workflows/${workflow === "daily" ? "daily" : workflow === "pipeline" ? "pipeline" : workflow === "rss" ? "rss" : "interests"}.yml`,head_branch:"main",event:workflow === "daily" ? "schedule" : "workflow_dispatch",status:"completed",conclusion:"success",created_at:"2026-11-04T03:09:55Z",run_started_at:"2026-11-04T03:09:55Z",updated_at:"2026-11-04T03:10:30Z",html_url:"https://github.com/iesilder/kw-plan-weekly-trend/actions/runs/41765377940",...extra};
  raw.html_url = `https://github.com/iesilder/kw-plan-weekly-trend/actions/runs/${raw.id}`;
  return Runs.normalizeRun(raw,workflow);
}
function model(records = [], at = NOW) {
  return {records,checkedAt:at,warning:"",cached:false,workflows:Object.fromEntries(["daily","pipeline","interests","rss"].map(workflow => [workflow,{complete:true,checkedAt:at,error:null,records:records.filter(record => record.workflow === workflow)}]))};
}
function harness({load = async () => model(),at = NOW,missingPanel = false} = {}) {
  let clock = Date.parse(at);
  const ids = new Map(),all = [],loads = [],intervals = [],timeouts = [],downloads = [],blobs = [],revoked = [],fetches = [];
  const listeners = {window:{},document:{}};
  class Node {
    constructor(tag = "div") { this.tagName = tag.toUpperCase();this.children = [];this.attrs = {};this.events = {};this.className = "";this.hidden = false;this.disabled = false;all.push(this); }
    set textContent(value) { this.text = String(value);this.children = []; }
    get textContent() { return (this.text || "") + this.children.map(child => child.textContent).join(""); }
    set innerHTML(_) { throw Error("Execution metadata must be inert text"); }
    append(...nodes) { this.children.push(...nodes);nodes.forEach(node => { node.parent = this; }); }
    replaceChildren(...nodes) { this.text = "";this.children = [];this.append(...nodes); }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
    setAttribute(name,value) { this.attrs[name] = String(value); }
    addEventListener(type,listener) { (this.events[type] ||= []).push(listener); }
    fire(type) { for (const listener of this.events[type] || []) listener({target:this}); }
    click() { downloads.push({href:this.href,download:this.download});this.fire("click"); }
  }
  function listen(target,type,listener) { (listeners[target][type] ||= []).push(listener); }
  const document = {visibilityState:"visible",body:new Node("body"),getElementById(id) { if (id === "batch-runs" && missingPanel) return null;if (!ids.has(id)) ids.set(id,new Node());return ids.get(id); },createElement:tag => new Node(tag),addEventListener:(type,listener) => listen("document",type,listener)};
  class ClockDate extends Date { constructor(...args) { super(...(args.length ? args : [clock])); } static now() { return clock; } }
  class TestBlob { constructor(parts,options) { this.parts = parts;this.type = options.type;blobs.push(this); } }
  class TestURL extends URL { static createObjectURL(blob) { assert.equal(blob,blobs.at(-1));return `blob:test-${blobs.length}`; } static revokeObjectURL(url) { revoked.push(url); } }
  const window = {WeeklyBatchRuns:{...Runs,deriveToday:value => Runs.deriveToday(value,clock),createClient:() => ({load:options => { loads.push({...options});return load(options); }})},addEventListener:(type,listener) => listen("window",type,listener)};
  const context = vm.createContext({window,document,Date:ClockDate,Intl,URL:TestURL,Blob:TestBlob,fetch:(...args) => { fetches.push(args);throw Error("No article or static snapshot dependency allowed"); },setInterval:(callback,delay) => { intervals.push({callback,delay});return intervals.length; },setTimeout:(callback,delay) => { timeouts.push({callback,delay});return timeouts.length; }});
  vm.runInContext(fs.readFileSync(require.resolve("../site/batch_runs_ui.js"),"utf8"),context);
  return {$:document.getElementById.bind(document),document,all,loads,intervals,timeouts,downloads,blobs,revoked,fetches,advance(ms) { clock += ms; },fire(target,type) { for (const listener of listeners[target][type] || []) listener(); },settle:tick};
}

test("today's absent schedule stays distinct from successful manual interest collection",async () => {
  const interest = run(),h = harness({load:async () => model([interest])});await h.settle();
  assert.equal(h.$("batch-today-state").textContent,"실행 기록 없음");
  assert.match(h.$("batch-today-note").textContent,/지연 가능성/);
  assert.match(h.$("batch-today-note").textContent,/실패로 확정한 상태는 아닙니다/);
  assert.equal(h.$("batch-latest-state").textContent,"관심사 요청 실행 · 성공");
  assert.match(h.$("batch-log-body").textContent,/관심사 요청 실행/);
  assert.match(h.$("batch-log-body").textContent,/11\.04 12:09:55/);
  assert.match(h.$("batch-log-body").textContent,/11\.04 12:10:30/);
  assert.equal(h.$("batch-log-count").textContent,"1건");
  assert.equal(h.$("batch-warning").hidden,true);
  assert.equal(h.loads.length,1);assert.equal(h.fetches.length,0);
});

test("a genuine scheduled failure and cancelled manual run remain visible without article data",async () => {
  const daily = run("daily",{id:2,conclusion:"failure",created_at:"2026-11-04T02:00:00Z",run_started_at:"2026-11-04T02:00:10Z",updated_at:"2026-11-04T02:04:00Z"});
  const cancelled = run("pipeline",{id:3,conclusion:"cancelled"});
  const h = harness({load:async () => model([cancelled,daily])});await h.settle();
  assert.equal(h.$("batch-today-state").textContent,"실패");
  assert.match(h.$("batch-today-note").textContent,/정기 자동 배치 · 시작 11\.04 11:00:10/);
  assert.match(h.$("batch-log-body").textContent,/실패/);assert.match(h.$("batch-log-body").textContent,/취소/);
  assert.equal(h.$("batch-latest-state").textContent,"일반 수동 수집 · 취소");
  assert.equal(h.fetches.length,0);
});

test("failed refresh invalidates today's old success and never displays raw errors",async () => {
  const daily = run("daily"),saved = model([daily]);let calls = 0;
  const h = harness({load:async () => { if (++calls === 1) return saved;throw Error("SECRET_RAW_RESPONSE https://private.invalid?token=SECRET"); }});await h.settle();
  assert.equal(h.$("batch-today-state").textContent,"성공");
  h.$("batch-refresh").fire("click");await h.settle();
  assert.equal(h.$("batch-today-state").textContent,"확인 불가");
  assert.match(h.$("batch-warning").textContent,/확인하지 못했습니다/);
  assert.equal(h.$("batch-warning").hidden,false);
  assert.doesNotMatch(h.all.map(node => node.textContent).join("\n"),/SECRET|private\.invalid|RAW_RESPONSE/);
  assert.match(h.$("batch-log-body").textContent,/정기 자동 배치/);
  assert.equal(h.$("batch-checked").textContent,"GitHub 실행 기록 확인 불가");
  assert.equal(h.$("batch-refresh").disabled,false);assert.equal(h.loads.at(-1).force,true);
});

test("metadata is inert text and normalized attempt-specific log links are safe",async () => {
  const record = run("daily",{id:42,run_attempt:2});
  record.label = '<img src=x onerror="alert(1)">';
  const h = harness({load:async () => model([record])});await h.settle();
  assert(h.$("batch-log-body").textContent.includes(record.label));
  assert.equal(h.all.filter(node => ["IMG","SVG","SCRIPT"].includes(node.tagName)).length,0);
  const links = h.all.filter(node => node.tagName === "A");
  assert.equal(links.length,1);assert.equal(links[0].href,"https://github.com/iesilder/kw-plan-weekly-trend/actions/runs/42/attempts/2");
  assert.equal(links[0].target,"_blank");assert.equal(links[0].rel,"noopener noreferrer");
  assert.match(links[0].attrs["aria-label"],/#42 시도 2 상세 로그/);
});

test("logs download contains normalized execution metadata and dated Korean schedule status",async () => {
  const record = run(),saved = model([record]),h = harness({load:async () => saved});await h.settle();
  h.$("batch-download").fire("click");
  assert.equal(h.blobs.length,1);assert.equal(h.blobs[0].type,"application/json;charset=utf-8");
  const exported = JSON.parse(h.blobs[0].parts.join(""));
  assert.equal(exported.repository,"iesilder/kw-plan-weekly-trend");assert.equal(exported.source,"GitHub Actions");assert.equal(exported.timezone,"Asia/Seoul");assert.equal(exported.exported_at,NOW);
  assert.equal(exported.today.dateKst,"2026-11-04");assert.equal(exported.today.state,"no_record");
  assert.deepEqual(exported.records,copy(saved.records));
  assert.deepEqual(Object.keys(exported.records[0]).sort(),["attempt","createdAt","event","id","kind","label","startedAt","state","updatedAt","url","workflow"].sort());
  assert.equal(h.downloads[0].download,"batch-runs-2026-11-04.json");assert.equal(h.downloads[0].href,"blob:test-1");
  assert.equal(h.document.body.children.length,0);h.timeouts.find(item => item.delay === 1000).callback();assert.deepEqual(h.revoked,["blob:test-1"]);
});

test("focus waits five minutes, hidden pages stay quiet, and the minute timer only renders",async () => {
  const h = harness({load:async () => model()});await h.settle();
  assert.equal(h.loads.length,1);assert.deepEqual(h.intervals.map(item => item.delay),[60000]);
  h.fire("window","focus");h.fire("document","visibilitychange");await h.settle();assert.equal(h.loads.length,1);
  h.advance(299999);h.fire("window","focus");await h.settle();assert.equal(h.loads.length,1);
  for (let i=0;i<10;i++) h.intervals[0].callback();assert.equal(h.loads.length,1);
  h.advance(1);h.document.visibilityState = "hidden";h.fire("window","focus");h.fire("document","visibilitychange");await h.settle();assert.equal(h.loads.length,1);
  h.document.visibilityState = "visible";h.fire("document","visibilitychange");await h.settle();assert.equal(h.loads.length,2);assert.equal(h.loads[1].force,false);
  h.fire("window","focus");await h.settle();assert.equal(h.loads.length,2);
  assert.equal(h.fetches.length,0);
});

test("an expired live check becomes unknown on render instead of preserving today's success",async () => {
  const h = harness({load:async () => model([run("daily")])});await h.settle();assert.equal(h.$("batch-today-state").textContent,"성공");
  h.advance(300001);h.intervals[0].callback();assert.equal(h.$("batch-today-state").textContent,"확인 불가");assert.equal(h.loads.length,1);
  assert.match(h.$("batch-latest-note").textContent,/11\.04 12:09:55/);
});

test("pre-schedule lookup cannot become an after-schedule no-record claim without a new lookup",async () => {
  const at = "2026-11-04T01:59:30Z",h = harness({at,load:async () => model([],at)});await h.settle();
  assert.equal(h.$("batch-today-state").textContent,"예정 · 대기");h.advance(60000);h.intervals[0].callback();
  assert.equal(h.$("batch-today-state").textContent,"확인 불가");assert.equal(h.loads.length,1);
});

test("missing panel does not fetch or install background timers",async () => {
  const h = harness({missingPanel:true});await h.settle();assert.equal(h.loads.length,0);assert.equal(h.intervals.length,0);
});


test("RSS saves and interest rebuild labels do not turn the daily schedule status into success",async () => {
  const rss = run("rss",{id:11,event:"schedule"});
  const rebuild = run("interests",{id:12,event:"issues",display_title:"[관심사] 관심사항 등록"});
  const h = harness({load:async () => model([rss,rebuild])});await h.settle();
  assert.equal(h.$("batch-today-state").textContent,"실행 기록 없음");
  assert.equal(h.$("batch-latest-state").textContent,"RSS 정기 저장 · 성공");
  assert.match(h.$("batch-log-body").textContent,/관심사 저장 자료 재분류/);
  assert.match(h.$("batch-log-body").textContent,/RSS 정기 저장/);
  assert.equal(h.fetches.length,0);
});
