"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const Review = require("../site/review.js");
const AI = require("../site/ai.js");
const Article = require("../site/article.js");
const Interests = require("../site/interests.js");
const Registry = require("../site/interest_registry.js");
const STAMP = "2026-11-04T03:10:00Z", A = "issue-"+"a".repeat(20), E = "version-"+"1".repeat(20), HASH = "b".repeat(64);
const SOURCE = "연합인포맥스(기사) · 증권", ARTICLE_URL = "https://news.einfomax.co.kr/news/articleView.html?idxno=1234567";
const tick = () => new Promise(resolve => setImmediate(resolve));
const clone = value => JSON.parse(JSON.stringify(value));

function fixture() {
  const candidate = {issue_id:A,title:"가상증권 AI 서비스 출시",company:"가상증권",topic_id:"digital",is_demo:false,analysis_status:"pending_ai",priority:"unranked",bullets:[],source_name:SOURCE,content_filter:"topics",evidence_ids:[E],detail_path:`data/issues/${A}.json`,published_date:"2026-11-04"};
  const latest = {schema_version:"1.0",generation_id:"live-main-test",generated_at:STAMP,report_week:"2026-W45",is_demo:false,candidates:[candidate]};
  const detail = {...candidate,generation_id:latest.generation_id,why_now:"새 서비스 출시라는 제목 표현으로 분류했습니다.",evidence:[{evidence_id:E,source_name:SOURCE,source_url:ARTICLE_URL,title:candidate.title,published_date:candidate.published_date,fetched_at:STAMP,content_level:"metadata_only"}]};
  const ai = {schema_version:"1.0",generation_id:latest.generation_id,generated_at:STAMP,provider:"google_gemini",model:"gemini-3.5-flash-lite",prompt_version:"metadata-review-v2",policy_version:"public-metadata-inference-v2",auto_analysis_enabled:true,last_attempt:null,counts:{eligible:1,blocked:0,completed:1,stale:0,failed:0,not_analyzed:0},items:{[A]:{status:"completed",reason_code:null,input_fingerprint:HASH,input_evidence_ids:[E],result_fingerprint:HASH,analyzed_at:STAMP,error_code:null,result:{suggested_topic_id:"digital",relevance:"relevant",rationale:"고객 서비스 변화가 있어 검토할 수 있습니다.",check_points:["실제 서비스 범위를 확인하세요."],duplicate_issue_ids:[]}}}};
  const article = {schema_version:"1.0",generation_id:latest.generation_id,generated_at:STAMP,provider:"google_gemini",model:"gemini-3.5-flash-lite",prompt_version:"article-bullets-v1",auto_analysis_enabled:true,last_attempt:null,counts:{eligible:1,completed:1,stale:0,unavailable:0,pending:0,not_applicable:0,failed:0},items:{[A]:{status:"completed",reason_code:null,evidence_ids:[E],input_fingerprint:HASH,result_fingerprint:HASH,analyzed_at:STAMP,source_url:ARTICLE_URL,source_name:SOURCE,body_hash:HASH,body_fetched_at:STAMP,truncated:false,result:{key_change:{text:"가상증권의 새 AI 고객 서비스 출시",evidence_ids:["p1"]},facts:[],stage:"출시·시행",why_candidate:"고객 접점 변화 검토 자료",evidence:[{id:"p1",quote:"새 AI 고객 서비스를 출시했다."}]}}}};
  const status = {is_demo:false,generation_id:latest.generation_id,published_generation_id:latest.generation_id,report_count:0,interests_count:0,ai_enabled:false,schedule_enabled:true,planned_collection_frequency:"daily_1100_kst",timezone:"Asia/Seoul",publication_status:"updated",sources:[]};
  const history = {is_demo:false,generation_id:latest.generation_id,reports:[]};
  const interests = {schema_version:"1.0",generation_id:latest.generation_id,generated_at:STAMP,registry:{status:"success",last_attempt_at:STAMP,last_success_at:STAMP,error_code:null},last_collection:null,interests:[]};
  return {latest,detail,ai,article,status,history,interests};
}

// Use real HTML identities: unlike older UI harnesses, missing moved panels stay null.
function harness({data = fixture(),fetchImpl,blockStorage = false,registryLoad} = {}) {
  const html = fs.readFileSync(require.resolve("../site/index.html"),"utf8");
  const ids = new Map(),all = [],fetches = [],storage = new Map(),missing = [],timers = new Map();let timerId=0;
  class Node {
    constructor(tag = "div") { this.tagName=tag.toUpperCase();this.children=[];this.attrs={};this.dataset={};this.events={};this.className="";this.value="";this.hidden=false;this.disabled=false;this.classList={toggle(){}};all.push(this); }
    set textContent(value) { this.text=String(value);this.children=[]; }
    get textContent() { return (this.text||"")+this.children.map(child=>child.textContent).join(""); }
    set innerHTML(_) { throw Error("Untrusted content must remain inert text"); }
    append(...nodes) { this.children.push(...nodes);nodes.forEach(node=>{node.parent=this;}); }
    prepend(...nodes) { this.children.unshift(...nodes);nodes.forEach(node=>{node.parent=this;}); }
    replaceChildren(...nodes) { this.text="";this.children=[];this.append(...nodes);if(this.tagName==="SELECT"&&!this.children.some(node=>node.value===this.value))this.value=this.children[0]?.value||""; }
    get firstChild() { return this.children[0]; } get options() { return this.children; } add(node) { this.append(node); }
    remove() { if(this.parent)this.parent.children=this.parent.children.filter(child=>child!==this); }
    setAttribute(key,value) { this.attrs[key]=String(value); } getAttribute(key) { return this.attrs[key]??null; }
    addEventListener(type,callback) { (this.events[type]||=[]).push(callback); }
    fire(type) { for(const callback of this.events[type]||[])callback({target:this}); }
    querySelector(selector) { return this.children.find(child=>matches(child,selector))||this.children.map(child=>child.querySelector(selector)).find(Boolean)||null; }
    scrollIntoView() {} showModal() { this.open=true; } close() { this.open=false; }
  }
  function matches(node,selector) {
    if(selector.startsWith("."))return node.className.split(" ").includes(selector.slice(1));
    if(selector==="[data-count]")return Object.hasOwn(node.dataset,"count");
    return node.tagName===selector.toUpperCase();
  }
  for(const match of html.matchAll(/<([a-z][a-z0-9-]*)(\s[^<>]*?)?\s*\/?>/gi)) {
    const node=new Node(match[1]),attrs=match[2]||"";
    for(const attr of attrs.matchAll(/([a-z][a-z0-9-]*)="([^"]*)"/gi)) {
      const [,name,value]=attr;node.attrs[name]=value;
      if(name==="id")ids.set(value,node);
      if(name==="class")node.className=value;
      if(name.startsWith("data-"))node.dataset[name.slice(5).replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase())]=value;
      if(["href","value"].includes(name))node[name]=value;
    }
  }
  const document = {visibilityState:"visible",getElementById(id){if(!ids.has(id))missing.push(id);return ids.get(id)||null;},createElement:tag=>new Node(tag),createTextNode:value=>{const node=new Node("#text");node.textContent=value;return node;},querySelectorAll:selector=>all.filter(node=>matches(node,selector)),addEventListener(){}};
  const paths = {"data/latest.json":data.latest,"data/status.json":data.status,"data/report-history.json":data.history,"data/reviews.json":Review.emptyCanonical(),"data/ai-analysis.json":data.ai,"data/article-analysis.json":data.article,"data/interests.json":data.interests,...Object.fromEntries((data.details||[data.detail]).map(detail=>[detail.detail_path,detail]))};
  const window = {WeeklyReview:Review,WeeklyAI:AI,WeeklyArticle:Article,WeeklyInterests:Interests,WeeklyInterestRegistry:{...Registry,createClient:()=>({load:async()=>registryLoad ? registryLoad() : clone(liveRegistry(data))})},addEventListener(){},open(){}};
  // Browser modules share one realm; parse saved drafts in the Review module's realm here too.
  const context = vm.createContext({window,document,Intl,Date,URL,JSON,Option:function(label,value){const node=new Node("option");node.textContent=label;node.value=value;return node;},localStorage:{getItem:key=>{if(blockStorage)throw Error("blocked storage");return storage.get(key)||null;},setItem:(key,value)=>{if(blockStorage)throw Error("blocked storage");storage.set(key,value);},removeItem:key=>storage.delete(key)},setTimeout:(callback,delay)=>{const id=++timerId;timers.set(id,{callback,delay});return id;},clearTimeout:id=>timers.delete(id),fetch:async(path,options)=>{fetches.push({path,options});const key=String(path).split("?")[0];const payload=fetchImpl ? await fetchImpl(String(path),options,paths) : paths[key];if(payload===undefined)throw Error(`Unexpected request: ${path}`);return {ok:true,json:async()=>clone(payload)};}});
  const source=fs.readFileSync(require.resolve("../site/app.js"),"utf8").replace(/  loadData\(\);\s*\}\)\(\);\s*$/,"  window.testApp={state,reviewState,aiState,articleState,interestsState,registryState,loadData,selectIssue,changeDecision,renderCandidates};\n  window.initialLoad=loadData();\n})();");
  vm.runInContext(source,context);
  return {html,app:window.testApp,$:id=>ids.get(id)||null,all,fetches,storage,missing,timers,expireReads(){for(const [id,timer] of [...timers])if(timer.delay===15000){timers.delete(id);timer.callback();}},async settle(){await window.initialLoad;for(let i=0;i<15;i++)await tick();}};
}


const B = "issue-"+"b".repeat(20);
const deferred = () => {let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
function generation(name,{interest=false}={}) {
  const data=fixture();
  for(const key of ["latest","status","history","ai","article","interests"]) data[key].generation_id=name;
  data.status.published_generation_id=name;data.detail.generation_id=name;
  data.latest.candidates[0].tags=["AI"];data.detail.tags=["AI"];
  const candidate={...clone(data.latest.candidates[0]),issue_id:B,title:"가상증권 ETF AI 점검",detail_path:`data/issues/${B}.json`};
  data.latest.candidates.push(candidate);
  const second={...clone(data.detail),...candidate,generation_id:name};data.details=[data.detail,second];
  for(const key of ["ai","article"]){data[key].items[B]=clone(data[key].items[A]);data[key].counts.eligible=2;data[key].counts.completed=2;}
  if(interest) data.interests.interests=[{id:"interest-10",issue_number:10,title:"AI 서비스",question:"AI 서비스의 변화는?",keywords:["AI"],exclude_keywords:[],topic_id:"digital",active_from:"2026-11-01",active_until:null,updated_at:STAMP,source_url:"https://github.com/iesilder/kw-plan-weekly-trend/issues/10",enabled:true,state:"active",last_collected_at:STAMP,collection_status:"success",match_count:2,matches:[{issue_id:A,keywords:["AI"]},{issue_id:B,keywords:["AI"]}]}];
  return data;
}
function liveRegistry(data) {
 const fields=["id","issue_number","title","question","keywords","exclude_keywords","topic_id","active_from","active_until","updated_at","source_url","enabled"];
 return {fetched_at:STAMP,interests:data.interests.interests.map(item=>Object.fromEntries(fields.map(key=>[key,item[key]])))};
}
function value(data,url) {
 const path=String(url).split("?")[0];
 return {"data/latest.json":data.latest,"data/status.json":data.status,"data/report-history.json":data.history,"data/reviews.json":Review.emptyCanonical(),"data/ai-analysis.json":data.ai,"data/article-analysis.json":data.article,"data/interests.json":data.interests,...Object.fromEntries(data.details.map(detail=>[detail.detail_path,detail]))}[path];
}
const detailCalls=h=>h.fetches.filter(call=>call.path.startsWith("data/issues/"));

test("a stale CDN detail gets one generation-pinned bypass retry without refreshing the list",async()=>{
 const data=generation("live-a");let requests=0;
 const h=harness({data,fetchImpl:async(url)=>{const result=value(data,url);if(url.startsWith("data/issues/")&&++requests===1)return {...result,generation_id:"old-cdn"};return result;}});await h.settle();
 assert.equal(h.app.state.detail.generation_id,"live-a");assert.equal(detailCalls(h).length,2);
 assert.match(detailCalls(h)[0].path,/\?generation=live-a$/);assert.match(detailCalls(h)[1].path,/generation=live-a&refresh=/);
 assert.equal(h.fetches.filter(call=>call.path.startsWith("data/latest.json")).length,1);assert.equal(h.$("error-banner").hidden,true);
});

test("an open old list recovers the same article on a new snapshot and preserves filters and saved or memory-only choices",async()=>{
 for(const blockStorage of [false,true]){
  const before=generation("live-a",{interest:true}),after=generation("live-b",{interest:true});let current=before;
  const h=harness({data:before,blockStorage,fetchImpl:async url=>value(current,url)});await h.settle();
  h.app.changeDecision(A,"accepted");h.app.changeDecision(B,"held");await h.settle();
  h.app.state.topic="digital";h.$("search").value="가상";h.$("company-filter").value="가상증권";h.$("tag-filter").value="AI";h.$("sort-order").value="title";h.$("review-filter").value="held";h.$("interest-filter").value="interest-10";h.app.interestsState.filter="interest-10";
  current=after;await h.app.selectIssue(h.app.state.latest.candidates.find(item=>item.issue_id===B));await h.settle();
  assert.equal(h.app.state.latest.generation_id,"live-b");assert.equal(h.app.state.selected,B);assert.equal(h.app.state.detail.generation_id,"live-b");
  assert.equal(h.app.state.topic,"digital");for(const [id,want] of [["search","가상"],["company-filter","가상증권"],["tag-filter","AI"],["sort-order","title"],["review-filter","held"],["interest-filter","interest-10"]])assert.equal(h.$(id).value,want,id);
  assert.deepEqual({...h.app.reviewState.draft.changes},{[A]:"accepted",[B]:"held"});assert.equal(h.app.reviewState.unsavedStorage,blockStorage);
  const refreshed=h.fetches.filter(call=>!call.path.startsWith("data/issues/")&&call.path.includes("refresh="));assert.equal(refreshed.length,7);assert.equal(new Set(refreshed.map(call=>new URL(call.path,"https://example.invalid/").searchParams.get("refresh"))).size,1);
  assert(h.fetches.every(call=>call.path.startsWith("data/")));assert.deepEqual(h.missing,[]);
 }
});

test("persistent generation, identity and mode mismatches stay blocked after one bounded recovery",async()=>{
 for(const changed of [{generation_id:"wrong"},{issue_id:A},{is_demo:true}]){
  const data=generation("live-a");let fail=false;
  const h=harness({data,fetchImpl:async url=>{const result=value(data,url);return fail&&url.startsWith(`data/issues/${B}`)?{...result,...changed}:result;}});await h.settle();fail=true;
  await h.app.selectIssue(h.app.state.latest.candidates.find(item=>item.issue_id===B));await h.settle();
  assert.equal(h.app.state.detail,null);assert.match(h.$("detail-panel").textContent,/상세를 표시할 수 없습니다/);assert.match(h.$("detail-panel").textContent,/목록·상세 다시 불러오기/);
  assert.equal(h.fetches.filter(call=>call.path.startsWith("data/latest.json")).length,2);assert.equal(detailCalls(h).filter(call=>call.path.includes(B)).length,4);
  await h.settle();assert.equal(h.fetches.filter(call=>call.path.startsWith("data/latest.json")).length,2);
 }
});

test("a late valid registry result cannot erase the recovered interest filter or delay article recovery",async()=>{
 const before=generation("live-a",{interest:true}),after=generation("live-b",{interest:true}),registry=deferred();let current=before;
 const h=harness({data:before,registryLoad:()=>registry.promise,fetchImpl:async url=>value(current,url)});await h.settle();
 h.$("interest-filter").value="interest-10";h.app.interestsState.filter="interest-10";current=after;
 await h.app.selectIssue(h.app.state.latest.candidates.find(item=>item.issue_id===B));await h.settle();assert.equal(h.app.state.detail.generation_id,"live-b");assert.equal(h.$("interest-filter").value,"interest-10");
 registry.resolve(liveRegistry(after));await h.settle();assert.equal(h.$("interest-filter").value,"interest-10");assert.equal(h.app.state.selected,B);assert.equal(h.app.state.detail.generation_id,"live-b");
});

test("a removed article is not silently replaced and late registry rendering cannot restart recovery",async()=>{
 const before=generation("live-a"),after=generation("live-b");after.latest.candidates=after.latest.candidates.filter(item=>item.issue_id!==B);after.details=after.details.filter(item=>item.issue_id!==B);
 for(const key of ["ai","article"]){delete after[key].items[B];after[key].counts.eligible=1;after[key].counts.completed=1;}
 let current=before;const registry=deferred();
 const h=harness({data:before,registryLoad:()=>registry.promise,fetchImpl:async url=>url.startsWith(`data/issues/${B}`)&&current===after?{...before.details[1],generation_id:"live-b"}:value(current,url)});await h.settle();current=after;
 await h.app.selectIssue(h.app.state.latest.candidates.find(item=>item.issue_id===B));await h.settle();assert.equal(h.app.state.selected,null);assert.match(h.$("detail-panel").textContent,/자료 목록이 갱신되었습니다/);
 registry.resolve(liveRegistry(after));await h.settle();assert.equal(h.app.state.selected,null);assert.match(h.$("detail-panel").textContent,/다른 기사를 자동으로 열지 않았습니다/);
});

test("switching articles while the old detail is late cannot replace the new selection",async()=>{
 const data=generation("live-a"),waiting=deferred();let delay=false;
 const h=harness({data,fetchImpl:async url=>delay&&url.startsWith(`data/issues/${B}`)?waiting.promise:value(data,url)});await h.settle();delay=true;
 const old=h.app.selectIssue(h.app.state.latest.candidates.find(item=>item.issue_id===B));await tick();await h.app.selectIssue(h.app.state.latest.candidates.find(item=>item.issue_id===A));waiting.resolve(data.details[1]);await old;await h.settle();
 assert.equal(h.app.state.selected,A);assert.equal(h.app.state.detail.issue_id,A);assert.equal(h.fetches.filter(call=>call.path.startsWith("data/latest.json")).length,1);
});

test("manual reload wins over an older automatic recovery response",async()=>{
 const before=generation("live-a"),after=generation("live-b"),newer=generation("live-c"),waiting=deferred();let current=before,delay=false;
 const h=harness({data:before,fetchImpl:async url=>delay&&url.startsWith("data/latest.json?refresh=")?waiting.promise:value(current,url)});await h.settle();current=after;delay=true;
 const old=h.app.selectIssue(h.app.state.latest.candidates.find(item=>item.issue_id===B));await h.settle();current=newer;delay=false;await h.app.loadData();await h.settle();waiting.resolve(after.latest);await old;await h.settle();
 assert.equal(h.app.state.latest.generation_id,"live-c");assert.equal(h.app.state.detail.generation_id,"live-c");assert.equal(h.$("reload-data").disabled,false);
});

test("new filter input during recovery is not overwritten by the earlier captured view",async()=>{
 const before=generation("live-a"),after=generation("live-b"),waiting=deferred();let current=before,delay=false;
 const h=harness({data:before,fetchImpl:async url=>delay&&url.startsWith("data/latest.json?refresh=")?waiting.promise:value(current,url)});await h.settle();current=after;delay=true;
 const work=h.app.selectIssue(h.app.state.latest.candidates.find(item=>item.issue_id===B));await h.settle();h.$("search").value="ETF";h.$("search").fire("input");delay=false;waiting.resolve(after.latest);await work;await h.settle();
 assert.equal(h.$("search").value,"ETF");assert.equal(h.app.state.selected,B);assert.equal(h.app.state.detail.generation_id,"live-b");
});

test("a stalled optional sidecar times out and cannot leave recovery permanently busy",async()=>{
 const before=generation("live-a"),after=generation("live-b"),waiting=deferred();let current=before;
 const h=harness({data:before,fetchImpl:async url=>current===after&&url.startsWith("data/article-analysis.json?refresh=")?waiting.promise:value(current,url)});await h.settle();current=after;
 const work=h.app.selectIssue(h.app.state.latest.candidates.find(item=>item.issue_id===B));await h.settle();assert.equal(h.$("reload-data").disabled,true);h.expireReads();await work;await h.settle();
 assert.equal(h.app.state.detail.generation_id,"live-b");assert.equal(h.$("reload-data").disabled,false);assert.equal(h.app.articleState.data,null);assert.equal(h.app.aiState.data.generation_id,"live-b");
 waiting.resolve(after.article);await h.settle();assert.equal(h.app.articleState.data,null);
});

test("review counters and interest-card shortcuts supersede a pending recovered detail",async()=>{
 for(const shortcut of ["review-counter-held","interest-filter-button"]){
  const before=generation("live-a",{interest:true}),after=generation("live-b",{interest:true}),waiting=deferred();let current=before;
  for(const data of [before,after]){const interest=data.interests.interests[0];interest.matches=interest.matches.filter(match=>match.issue_id===A);interest.match_count=1;}
  const h=harness({data:before,fetchImpl:async url=>current===after&&url.startsWith(`data/issues/${B}`)&&new URL(url,"https://example.invalid/").searchParams.get("generation")==="live-b"?waiting.promise:value(current,url)});await h.settle();
  h.app.changeDecision(A,"held");current=after;
  const old=h.app.selectIssue(h.app.state.latest.candidates.find(item=>item.issue_id===B));await h.settle();
  assert.equal(h.app.state.latest.generation_id,"live-b");assert.equal(h.app.state.selected,B);assert(h.app.state.selectionPaused);
  const button=h.all.filter(node=>node.className.split(" ").includes(shortcut)).at(-1);assert(button);button.fire("click");await h.settle();
  assert.equal(h.app.state.selected,A);assert.equal(h.app.state.detail.issue_id,A);
  waiting.resolve(after.details[1]);await old;await h.settle();assert.equal(h.app.state.selected,A);assert.equal(h.app.state.detail.issue_id,A);
  if(shortcut==="review-counter-held")assert.equal(h.$("review-filter").value,"held");else assert.equal(h.$("interest-filter").value,"interest-10");
 }
});
