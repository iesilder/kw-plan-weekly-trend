"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const Interests = require("../site/interests.js");
const Registry = require("../site/interest_registry.js");
const Review = require("../site/review.js");
const A="issue-"+"a".repeat(20),B="issue-"+"b".repeat(20),NOW="2026-11-04T01:00:00Z",BEFORE="2026-11-01T00:00:00Z";
const clone=value=>JSON.parse(JSON.stringify(value));
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function record(number=10,extra={}) {return {id:`interest-${number}`,issue_number:number,title:"ETF 사업",question:"ETF 사업의 변화는?",keywords:["ETF"],exclude_keywords:[],topic_id:"strategy",active_from:"2026-11-01",active_until:null,updated_at:BEFORE,source_url:`https://github.com/iesilder/kw-plan-weekly-trend/issues/${number}`,enabled:true,...extra};}
function fixture() {
  const latest={generation_id:"live-ui",generated_at:NOW,report_week:"2026-W45",is_demo:false,candidates:[{issue_id:A,title:"증권사 ETF 출시",company:"증권업권 관련",topic_id:"digital",is_demo:false,content_filter:"topics",source_name:"연합인포맥스(기사) · 증권",bullets:[],analysis_status:"pending_ai",priority:"unranked"}]};
  const published={schema_version:"1.0",generation_id:latest.generation_id,generated_at:NOW,registry:{status:"success",last_attempt_at:BEFORE,last_success_at:BEFORE,error_code:null},last_collection:null,interests:[{...record(),state:"active",last_collected_at:BEFORE,collection_status:"success",match_count:1,matches:[{issue_id:A,keywords:["ETF"]}]}]};
  return {latest,published,live:{fetched_at:NOW,interests:[record()]}};
}
function harness({load=async()=>({fetched_at:NOW,interests:[]}),createClient,fetchImpl}={}) {
  const all=[],ids=new Map(),loads=[],fetches=[],events={window:{},document:{}};
  class Node {
    constructor(tag="div",value=""){this.tagName=tag.toUpperCase();this.children=[];this.attrs={};this.dataset={};this.events={};this.className="";this.value=value;this.disabled=false;this.hidden=false;this.classList={toggle(){}};all.push(this);}
    set textContent(value){this.children=[];this.text=String(value);} get textContent(){return (this.text||"")+this.children.map(node=>node.textContent).join("");}
    set innerHTML(value){throw Error("Interest text must not be interpreted as HTML");}
    append(...nodes){this.children.push(...nodes);nodes.forEach(node=>node.parent=this);} prepend(...nodes){this.children.unshift(...nodes);nodes.forEach(node=>node.parent=this);}
    replaceChildren(...nodes){this.text="";this.children=[];this.append(...nodes);if(this.tagName==="SELECT"&&!this.children.some(node=>node.value===this.value))this.value=this.children[0]?.value||"";}
    get firstChild(){return this.children[0];} get options(){return this.children;} add(node){this.append(node);} remove(){if(this.parent)this.parent.children=this.parent.children.filter(node=>node!==this);}
    setAttribute(key,value){this.attrs[key]=String(value);} getAttribute(key){return this.attrs[key];} scrollIntoView(){}
    addEventListener(type,callback){(this.events[type]||=[]).push(callback);} fire(type){for(const callback of this.events[type]||[])callback({target:this});}
  }
  const add=(target,type,callback)=>(events[target][type]||=[]).push(callback);
  const document={visibilityState:"visible",addEventListener:(type,callback)=>add("document",type,callback),getElementById(id){if(!ids.has(id))ids.set(id,new Node(id.includes("filter")||id==="sort-order"?"select":"div"));return ids.get(id);},createElement:tag=>new Node(tag),createTextNode:value=>{const node=new Node("#text");node.textContent=value;return node;},querySelectorAll:selector=>all.filter(node=>selector.startsWith(".")&&node.className.split(" ").includes(selector.slice(1)))};
  const facade={...Registry,createClient:args=>createClient?createClient(args):{load:options=>{loads.push(options);return load(options);}}};
  const window={WeeklyInterests:Interests,WeeklyInterestRegistry:facade,WeeklyReview:Review,addEventListener:(type,callback)=>add("window",type,callback),open(){}};
  const localStorage={getItem(){return null;},setItem(){},removeItem(){}};
  const context=vm.createContext({window,document,localStorage,Option:function(label,value){const node=new Node("option",value);node.textContent=label;return node;},Intl,Date,URL,setTimeout:()=>0,clearTimeout(){},fetch:(...args)=>{fetches.push(args);return fetchImpl?fetchImpl(...args):Promise.reject(Error("No static data in this test"));}});
  const source=fs.readFileSync(require.resolve("../site/app.js"),"utf8").replace(/  loadData\(\);\s*\}\)\(\);\s*$/,"  window.testApp={state,registryState,interestsState,reviewState,aiState,articleState,initializeInterests,rebuildInterestView,renderInterests,refreshLiveInterests,loadData};\n})();");
  vm.runInContext(source,context);
  return {app:window.testApp,$:document.getElementById.bind(document),all,loads,fetches,document,fire(target,type){for(const callback of events[target][type]||[])callback();},async settle(){await tick();while(window.testApp.registryState.pending){await window.testApp.registryState.pending;await tick();}}};
}
test("live registration cards render independently before articles and still render after article JSON fails",async()=>{
  const {live}=fixture(),h=harness({load:async()=>live});
  const articleLoad=h.app.loadData();await h.settle();await articleLoad;
  assert.equal(h.app.state.latest,null);assert.equal(h.app.interestsState.data.registration_live,true);assert.equal(h.app.interestsState.data.interests[0].registration_status,"pending");
  assert.match(h.$("interest-cards").textContent,/등록됨 · 수집 대기/);assert.match(h.$("interest-cards").textContent,/관련 기사 없음으로 판단하지 않습니다/);assert.doesNotMatch(h.$("interest-cards").textContent,/관련 기사 보기 \(0\)/);assert.match(h.$("interests-registry-checked").textContent,/최근 확인한 등록 목록/);assert.equal(h.$("interest-filter").disabled,true);
});
test("a live registry arriving before the article snapshot is safely recombined with the eventual snapshot",async()=>{
  const {latest,published,live}=fixture(),waiting=deferred();latest.candidates=[];published.interests=[];
  const status={is_demo:false,generation_id:latest.generation_id,report_count:0,interests_count:0,ai_enabled:false,schedule_enabled:false,sources:[]};
  const h=harness({load:async()=>live,fetchImpl:async path=>({ok:true,json:async()=>path==="data/latest.json"?waiting.promise:path==="data/status.json"?status:path==="data/report-history.json"?{is_demo:false,generation_id:latest.generation_id,reports:[]}:path==="data/reviews.json"?Review.emptyCanonical():path==="data/interests.json"?published:{}})});
  const articleLoad=h.app.loadData();await h.settle();assert.equal(h.app.state.latest,null);assert.equal(h.app.interestsState.data.interests.length,1);
  waiting.resolve(latest);await articleLoad;await h.settle();assert.equal(h.app.state.latest.generation_id,latest.generation_id);assert.equal(h.app.interestsState.data.generation_id,latest.generation_id);assert.match(h.$("interest-cards").textContent,/등록됨 · 수집 대기/);
});
test("API failure uses the validated published fallback and does not reset selected article, AI or review choices",async()=>{
  const {latest,published}=fixture(),h=harness({load:async()=>{throw Object.assign(Error("SECRET RAW_RESPONSE"),{code:"network_error"});}});
  h.app.state.latest=latest;h.app.state.selected=A;h.app.state.detail={issue_id:A};h.app.initializeInterests({value:published});
  const canonical=Review.emptyCanonical(),draft=Review.setDecision(canonical,Review.createDraft(canonical),A,"accepted"),ai={items:{[A]:{status:"completed"}}},article={items:{[A]:{status:"completed"}}};
  Object.assign(h.app.reviewState,{ready:true,canonical,draft});h.app.aiState.data=ai;h.app.articleState.data=article;
  await h.app.refreshLiveInterests();
  assert.equal(h.app.state.selected,A);assert.equal(h.app.state.detail.issue_id,A);assert.equal(h.app.reviewState.draft,draft);assert.equal(h.app.aiState.data,ai);assert.equal(h.app.articleState.data,article);assert.equal(h.app.interestsState.data.interests[0].matches[0].issue_id,A);
  assert.match(h.$("interests-warning").textContent,/마지막 배포에 저장된 목록/);assert.doesNotMatch(h.$("interests-warning").textContent,/SECRET|RAW_RESPONSE/);assert.equal(h.$("interests-collect").disabled,false);
});
test("transient API failure retains last-good live cards and their distinct registration confirmation time",async()=>{
  const {live}=fixture();let fail=false;const h=harness({load:async()=>{if(fail)throw Object.assign(Error("raw"),{code:"rate_limited"});return live;}});
  await h.app.refreshLiveInterests();fail=true;await h.app.refreshLiveInterests({force:true});
  assert.equal(h.app.interestsState.data.interests.length,1);assert.equal(h.app.registryState.live.fetched_at,NOW);assert.match(h.$("interests-warning").textContent,/조회 제한.*마지막으로 확인한 등록 목록/);assert.match(h.$("interests-registry-checked").textContent,/마지막으로 확인한 등록 목록/);assert.match(h.$("interests-last-run").textContent,/수집 이력 확인 전/);assert.equal(h.$("interests-refresh").disabled,false);
});
function publicIssue() {
  const values={"관심 주제":"ETF 사업","보고 질문":"ETF 사업의 변화는?","검색어":"ETF","제외 검색어":"_No response_","분류":"조직·경영전략","시작일":"2026-11-01","종료일":"_No response_","공개 저장 동의":"- [X] 관심사와 검색어가 공개 GitHub 이슈와 웹사이트에 저장됨을 이해합니다."};
  return {number:10,title:"[관심사] 관심사항 등록",body:Object.entries(values).map(([heading,value])=>`### ${heading}\n\n${value}`).join("\n\n"),state:"open",user:{login:"iesilder"},html_url:record().source_url,created_at:BEFORE,updated_at:BEFORE};
}
test("returning from registration within five seconds forces one refresh, while ordinary focus keeps the minute cache",async()=>{
  let clock=Date.parse(NOW),payload=[],requests=0;
  const h=harness({createClient:args=>Registry.createClient({...args,now:()=>new Date(clock)}),fetchImpl:async()=>{requests++;return {ok:true,status:200,headers:{get:()=>null},text:async()=>JSON.stringify(payload)};}});
  await h.app.refreshLiveInterests();assert.equal(h.app.interestsState.data.interests.length,0);assert.equal(requests,1);
  h.$("interests-register").fire("click");payload=[publicIssue()];clock+=5000;h.fire("window","focus");h.fire("document","visibilitychange");await h.settle();
  assert.equal(requests,2);assert.equal(h.app.interestsState.data.interests.length,1);assert.equal(h.app.registryState.dirty,false);assert.match(h.$("interest-cards").textContent,/등록됨 · 수집 대기/);
  h.fire("window","focus");await h.settle();assert.equal(requests,2);clock+=61000;h.fire("window","focus");await h.settle();assert.equal(requests,3);assert(h.fetches.every(([url])=>String(url).startsWith("https://api.github.com/repos/iesilder/kw-plan-weekly-trend/issues?")));
});
test("manual registration refresh and reload-data both explicitly refresh, without a polling timer",async()=>{
  const h=harness();h.$("interests-refresh").fire("click");await h.settle();assert.equal(h.loads.length,1);assert.equal(h.loads[0].force,true);assert.equal(h.fetches.length,0);
  h.$("reload-data").fire("click");await h.settle();assert.equal(h.loads.length,2);assert.equal(h.loads[1].force,true);assert(h.fetches.some(([path])=>path==="data/latest.json"));
});
test("a late live response uses the current article generation and published matches rather than captured old state",async()=>{
  const {latest,published,live}=fixture(),waiting=deferred(),h=harness({load:()=>waiting.promise});h.app.state.latest=latest;h.app.state.selected=A;h.app.initializeInterests({value:published});
  const request=h.app.refreshLiveInterests();const newer=clone(latest),newPublished=clone(published);newer.generation_id="live-new";newer.candidates[0].issue_id=B;newPublished.generation_id=newer.generation_id;newPublished.interests[0].matches[0].issue_id=B;h.app.state.latest=newer;h.app.state.selected=B;h.app.initializeInterests({value:newPublished});waiting.resolve(live);await request;
  assert.equal(h.app.interestsState.data.generation_id,"live-new");assert.deepEqual(h.app.interestsState.data.interests[0].matches,[{issue_id:B,keywords:["ETF"]}]);assert.equal(h.app.state.selected,B);
});
test("newer published registration data triggers at most one forced freshness check per article load",async()=>{
  const {latest,published,live}=fixture();published.registry.last_success_at="2026-11-04T01:00:10Z";published.registry.last_attempt_at=published.registry.last_success_at;published.generated_at=published.registry.last_success_at;
  const h=harness({load:async()=>({fetched_at:NOW,interests:[]})});h.app.state.latest=latest;h.app.state.selected=A;h.app.state.loadRequest=7;h.app.registryState.live=live;h.app.initializeInterests({value:published});await h.settle();
  assert.equal(h.loads.length,1);assert.equal(h.loads[0].force,true);assert.equal(h.app.interestsState.data.interests.length,1);assert.equal(h.app.interestsState.data.registry_view_status,"stale");assert.match(h.$("interests-warning").textContent,/게시된 등록 목록.*최신/);
  h.app.initializeInterests({value:published});await h.settle();assert.equal(h.loads.length,1);
});
test("the freshness guard also runs when a stale live response arrives after the newer published registry",async()=>{
  const {latest,published}=fixture(),waiting=deferred();published.registry.last_success_at="2026-11-04T01:00:10Z";published.registry.last_attempt_at=published.registry.last_success_at;published.generated_at=published.registry.last_success_at;
  let calls=0;const h=harness({load:()=>++calls===1?waiting.promise:Promise.resolve({fetched_at:NOW,interests:[]})});h.app.state.latest=latest;h.app.state.selected=A;h.app.state.loadRequest=4;
  h.app.refreshLiveInterests();h.app.initializeInterests({value:published});waiting.resolve({fetched_at:NOW,interests:[]});await h.settle();
  assert.equal(h.loads.length,2);assert.equal(h.loads[1].force,true);assert.equal(h.app.interestsState.data.interests.length,1);assert.equal(h.app.interestsState.data.registry_view_status,"stale");
});
test("new and changed registrations have no zero-result filter and untrusted live titles remain inert text",async()=>{
  const {latest,published,live}=fixture(),marker='<img src=x onerror="alert(1)">';live.interests[0].title=marker;live.interests.push(record(11));
  const h=harness({load:async()=>live});h.app.state.latest=latest;h.app.state.selected=A;h.app.initializeInterests({value:published});await h.app.refreshLiveInterests();
  assert.match(h.$("interest-cards").textContent,/변경됨 · 수집 대기/);assert.match(h.$("interest-cards").textContent,/등록됨 · 수집 대기/);assert(h.$("interest-cards").textContent.includes(marker));assert.equal(h.all.filter(node=>["IMG","SVG","SCRIPT"].includes(node.tagName)).length,0);assert.equal(h.$("interest-filter").disabled,true);assert.deepEqual(h.app.interestsState.data.interests.map(item=>item.matches),[[],[]]);
});
