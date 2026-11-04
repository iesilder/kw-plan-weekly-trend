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
function harness({data = fixture(),fail = [],live = {fetched_at:STAMP,interests:[]}} = {}) {
  const html = fs.readFileSync(require.resolve("../site/index.html"),"utf8");
  const ids = new Map(),all = [],fetches = [],storage = new Map(),missing = [];
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
  const paths = {"data/latest.json":data.latest,"data/status.json":data.status,"data/report-history.json":data.history,"data/reviews.json":Review.emptyCanonical(),"data/ai-analysis.json":data.ai,"data/article-analysis.json":data.article,"data/interests.json":data.interests,[data.latest.candidates[0]?.detail_path]:data.detail};
  let financeMode=false;const windowEvents=new Map();
  const window = {WeeklyFinanceAll:{controller:{isActive:()=>financeMode}},WeeklyReview:Review,WeeklyAI:AI,WeeklyArticle:Article,WeeklyInterests:Interests,WeeklyInterestRegistry:{...Registry,createClient:()=>({load:async()=>clone(live)})},addEventListener:(name,fn)=>{(windowEvents.get(name)||windowEvents.set(name,[]).get(name)).push(fn);},open(){}};
  const context = vm.createContext({window,document,Intl,Date,URL,Option:function(label,value){const node=new Node("option");node.textContent=label;node.value=value;return node;},localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},setTimeout:()=>0,clearTimeout(){},fetch:async(path,options)=>{fetches.push({path,options});path=String(path).split("?")[0];if(fail.includes(path))throw Error("request unavailable");if(!Object.hasOwn(paths,path))throw Error(`Unexpected request: ${path}`);return {ok:true,json:async()=>clone(paths[path])};}});
  const source=fs.readFileSync(require.resolve("../site/app.js"),"utf8").replace(/  loadData\(\);\s*\}\)\(\);\s*$/,"  window.testApp={state,reviewState,aiState,articleState,loadData};\n  window.initialLoad=loadData();\n})();");
  vm.runInContext(source,context);
  return {html,app:window.testApp,setFinance(active){financeMode=active;for(const fn of windowEvents.get("weekly:finance-view")||[])fn({detail:{active}});},$:id=>ids.get(id)||null,all,fetches,storage,missing,async settle(){await window.initialLoad;for(let i=0;i<5;i++)await tick();}};
}

test("main boots without administrative DOM, retains content summaries and lets the human select report candidates",async()=>{
  const h=harness();await h.settle();
  for(const id of ["batch-runs","ai-assistance","article-assistance","mode-notice","source-status-panel","issue-count","last-run"]){assert.equal(h.$(id),null);}
  assert.equal(h.$("error-banner").hidden,true);assert.deepEqual(h.missing,[]);
  assert.equal(h.app.state.latest.candidates.length,1);assert.match(h.$("candidate-list").textContent,/가상증권 AI 서비스 출시/);
  assert.match(h.$("detail-panel").textContent,/고객 서비스 변화가 있어 검토할 수 있습니다/);
  assert.match(h.$("detail-panel").textContent,/가상증권의 새 AI 고객 서비스 출시/);
  assert.match(h.$("connection-status").textContent,/관리페이지/);
  const accept=h.all.filter(node=>node.className.split(" ").includes("review-option-accepted")).at(-1);
  assert.equal(accept.disabled,false);accept.fire("click");await tick();
  assert.equal(h.app.reviewState.draft.changes[A],"accepted");assert(h.storage.size>0);
  assert.match(h.$("weekly-list").textContent,/가상증권 AI 서비스 출시/);
  assert.equal(h.app.aiState.data.items[A].status,"completed");assert.equal(h.app.articleState.data.items[A].status,"completed");
  assert(h.fetches.every(({path})=>path.startsWith("data/")));assert.deepEqual(h.missing,[]);
});

test("failed or stale title analysis cannot prevent article content and human review on the main page",async()=>{
  for(const stale of [false,true]) {
    const data=fixture();if(stale)data.ai.generation_id="old-generation";
    const h=harness({data,fail:stale?[]:["data/ai-analysis.json"]});await h.settle();
    assert.equal(h.app.state.latest.generation_id,data.latest.generation_id);assert.equal(h.app.aiState.data,null);
    assert.match(h.$("detail-panel").textContent,/가상증권의 새 AI 고객 서비스 출시/);
    assert.doesNotMatch(h.$("detail-panel").textContent,/고객 서비스 변화가 있어 검토할 수 있습니다/);
    const hold=h.all.filter(node=>node.className.split(" ").includes("review-option-held")).at(-1);hold.fire("click");
    assert.equal(h.app.reviewState.draft.changes[A],"held");assert.deepEqual(h.missing,[]);
  }
});

test("main loading failure keeps interest registration usable and directs diagnostics to management",async()=>{
  const h=harness({fail:["data/latest.json"]});await h.settle();
  assert.equal(h.app.state.latest,null);assert.equal(h.$("error-banner").hidden,false);
  assert.match(h.$("candidate-list").textContent,/관리페이지/);assert.match(h.$("detail-panel").textContent,/관리페이지/);
  assert.equal(h.$("interests-collect").disabled,false);assert.equal(h.$("interests-register").href,Interests.registrationURL());
  assert.equal(h.$("reload-data").disabled,false);assert.deepEqual(h.missing,[]);
});


test("finance browsing preserves review drafts and returns to the previous normal filters",async()=>{
  const h=harness();await h.settle();
  const accept=h.all.filter(node=>node.className.split(" ").includes("review-option-accepted")).at(-1);
  accept.fire("click");await tick();h.app.state.topic="digital";h.$("search").value="가상";
  const draft=JSON.stringify(h.app.reviewState.draft),before=h.fetches.length;
  h.setFinance(true);h.app.state.latest.candidates[0].title="가상증권 AI 서비스 출시";
  h.setFinance(false);await tick();
  assert.equal(JSON.stringify(h.app.reviewState.draft),draft);assert.equal(h.app.state.topic,"digital");assert.equal(h.$("search").value,"가상");
  assert.equal(h.fetches.length,before);assert.match(h.$("detail-panel").textContent,/가상증권/);
});
