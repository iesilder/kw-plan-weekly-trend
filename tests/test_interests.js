"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const Interests = require("../site/interests.js");
const Review = require("../site/review.js");
const A = "issue-"+"a".repeat(20), B = "issue-"+"b".repeat(20), DART = "issue-"+"c".repeat(20);
const STAMP = "2026-11-03T05:00:00Z", SOURCE = "연합인포맥스(기사) · 증권";
const clone = value => JSON.parse(JSON.stringify(value));
function fixture() {
  const article = {issue_id:A,title:"증권사 ETF 자본시장 서비스 확대",company:"증권업권 관련",topic_id:"digital",source_name:SOURCE,content_filter:"topics",is_demo:false,bullets:[],tags:[],published_date:"2026-11-03"};
  const latest = {schema_version:"1.0",generation_id:"live-test",is_demo:false,candidates:[article,{...article,issue_id:B,title:"해외 제휴 추진"},{...article,issue_id:DART,title:"ETF 실적 공시",source_name:"DART · 증권사",content_filter:"earnings_only"}]};
  const record = {id:"interest-12",issue_number:12,title:"ETF 사업",question:"ETF 사업과 자본시장 변화는 무엇인가?",keywords:["ETF","자본 시장"],exclude_keywords:["실적"],topic_id:"strategy",active_from:"2026-11-01",active_until:null,updated_at:STAMP,source_url:"https://github.com/iesilder/kw-plan-weekly-trend/issues/12",enabled:true,state:"active",last_collected_at:STAMP,collection_status:"success",match_count:1,matches:[{issue_id:A,keywords:["ETF","자본 시장"]}]};
  const data = {schema_version:"1.0",generation_id:latest.generation_id,generated_at:STAMP,registry:{status:"success",last_attempt_at:STAMP,last_success_at:STAMP,error_code:null},last_collection:{mode:"manual",started_at:STAMP,completed_at:STAMP,status:"success",request_url:"https://github.com/iesilder/kw-plan-weekly-trend/issues/13",error_code:null},interests:[record]};
  return {data,latest};
}
test("public interests validate without changing source articles or human choices",() => {
  const {data,latest} = fixture(), before = clone({data,latest});
  const result = Interests.validateSidecar(data,latest);
  assert.equal(result.interests[0].matches[0].issue_id,A);
  assert.equal(result.interests[0].topic_id,"strategy"); // Existing article classification stays independent.
  assert.deepEqual({data,latest},before);
  assert.equal(Interests.summary(result).activeCount,1);
});
test("registration and collection open distinct repository forms; management searches public interest titles",() => {
  const registration = new URL(Interests.registrationURL()), collection = new URL(Interests.collectionURL()), management = new URL(Interests.managementURL());
  for (const url of [registration,collection,management]) assert.equal(url.origin+url.pathname,"https://github.com/iesilder/kw-plan-weekly-trend/issues"+(url === management ? "" : "/new"));
  assert.equal(registration.searchParams.get("template"),"executive-interest.yml");
  assert.equal(collection.searchParams.get("template"),"interest-collection.yml");
  assert.equal(management.searchParams.get("q"),'is:issue "[관심사]" in:title');
});
test("issue URLs bind the exact host, repository, path and registration number",() => {
  const valid = fixture().data.interests[0].source_url;
  assert.equal(Interests.issueURL(valid,12),valid);
  assert.equal(Interests.issueURL(valid,13),null);
  for (const url of ["javascript:alert(1)",valid.replace("https:","http:"),valid.replace("github.com","github.com.evil.test"),valid.replace("github.com","name@github.com"),valid.replace("github.com","github.com:443"),valid.replace("kw-plan-weekly-trend","other"),valid+"?token=secret",valid+"#body",valid+"/",valid+"\n",valid.replace("/12","/0"),valid.replace("/12","/012"),valid.replace("/12","/10000000000")]) assert.equal(Interests.issueURL(url),null,url);
});
test("stale generation and malformed identities never attach interests to current articles",() => {
  for (const change of [d=>d.generation_id="old",d=>d.interests.push(clone(d.interests[0])),d=>d.interests[0].id="interest-13",d=>d.interests[0].issue_number="12",d=>d.interests[0].source_url=d.interests[0].source_url.replace("12","13"),d=>d.interests[0].match_count=0,d=>d.interests[0].matches.push(clone(d.interests[0].matches[0])),d=>d.interests[0].matches[0].issue_id="issue-"+"f".repeat(20)]) { const {data,latest}=fixture();change(data);assert.throws(()=>Interests.validateSidecar(data,latest)); }
});
test("only configured RSS news articles can be linked; DART, demo, mixed and unknown sources fail closed",() => {
  for (const change of [(d,l)=>d.interests[0].matches[0].issue_id=DART,(d,l)=>l.candidates[0].source_name="새 언론사",(d,l)=>l.candidates[0].is_demo=true,(d,l)=>l.is_demo=true,(d,l)=>l.candidates[0].body="원문 전체",(d,l)=>l.candidates[0].evidence=[{source_name:"DART · 증권사"}]]) { const {data,latest}=fixture();change(data,latest);assert.throws(()=>Interests.validateSidecar(data,latest)); }
});
test("matching uses literal title keywords, ASCII word boundaries, Korean whitespace and exclusion vetoes",() => {
  for (const [title,keyword,expected] of [["ETF 출시","etf",true],["ETFs 출시","ETF",false],["XETF 출시","ETF",false],["ETF_시장","ETF",true],["자본시장 확대","자본 시장",true],["자본\u0085시장 확대","자본 시장",true],["ſtock 제휴","stock",true],["ﬁntech 사업","fintech",true],["가상 사업","디지털",false],["a+b 출시","a+b",true]]) assert.equal(Interests.keywordMatches(title,keyword),expected,`${title}/${keyword}`);
  const record=fixture().data.interests[0];
  assert.deepEqual(Interests.matchingKeywords("ETF 실적 확대",record),[]);
  for (const change of [d=>d.interests[0].matches[0].keywords=["신규"],d=>d.interests[0].matches[0].keywords=["ETF"],d=>d.interests[0].exclude_keywords=["서비스"]]) { const {data,latest}=fixture();change(data);assert.throws(()=>Interests.validateSidecar(data,latest)); }
});
test("calendar states use the published Korean date, inclusive start and end, and inactive cards have no matches",() => {
  const {data,latest}=fixture();data.generated_at="2026-11-02T15:00:00Z";data.interests[0].active_from="2026-11-03";data.interests[0].active_until="2026-11-03";assert.doesNotThrow(()=>Interests.validateSidecar(data,latest));
  for (const [change,state] of [[r=>r.enabled=false,"disabled"],[r=>r.active_from="2026-11-04","disabled"],[r=>r.active_until="2026-11-02","expired"]]) { const {data,latest}=fixture();change(data.interests[0]);data.interests[0].state=state;data.interests[0].collection_status=state;data.interests[0].matches=[];data.interests[0].match_count=0;assert.doesNotThrow(()=>Interests.validateSidecar(data,latest));data.interests[0].matches=[{issue_id:A,keywords:["ETF","자본 시장"]}];data.interests[0].match_count=1;assert.throws(()=>Interests.validateSidecar(data,latest)); }
});
test("bounded normalized records reject invalid dates, text, flags, duplicate words and unsafe errors",() => {
  for (const change of [d=>d.generated_at="2026-02-30T00:00:00Z",d=>d.interests[0].active_until="2026-10-01",d=>d.interests[0].title="x".repeat(121),d=>d.interests[0].question="",d=>d.interests[0].title=" title ",d=>d.interests[0].question="line\nline",d=>d.interests[0].keywords=["ETF","etf"],d=>d.interests[0].keywords=Array(11).fill("ETF"),d=>d.interests[0].enabled=1,d=>d.interests[0].collection_status="done",d=>d.registry.status="pending",d=>d.registry.error_code="<script>raw response</script>",d=>d.last_collection.completed_at="2026-11-02T00:00:00Z",d=>d.last_collection.request_url="https://evil.test/issues/13",d=>d.last_collection.status="running"]) { const {data,latest}=fixture();change(data);assert.throws(()=>Interests.validateSidecar(data,latest)); }
});
test("successful empty, unsynced and failed registries remain distinguishable and all allow a fresh GitHub request",() => {
  const {data,latest}=fixture();data.interests=[];const empty=Interests.summary(Interests.validateSidecar(data,latest));assert.equal(empty.activeCount,0);assert.match(empty.label,/없음/);assert.equal(empty.canRequest,true);
  data.registry={status:"never_synced",last_attempt_at:null,last_success_at:null,error_code:null};data.last_collection=null;const pending=Interests.summary(Interests.validateSidecar(data,latest));assert.match(pending.label,/확인 전/);assert.match(pending.note,/0건으로 판단하지 않습니다/);assert.equal(pending.canRequest,true);
  data.registry={status:"failed",last_attempt_at:STAMP,last_success_at:null,error_code:"network_error"};const failed=Interests.summary(Interests.validateSidecar(data,latest));assert.match(failed.label,/확인 필요/);assert.match(failed.note,/확인하지 못했습니다/);assert.equal(failed.canRequest,true);
});
test("interest filtering composes with the current list and resets without mutating articles",() => {
  const {data,latest}=fixture(),valid=Interests.validateSidecar(data,latest),before=clone(latest.candidates);
  assert.deepEqual(Interests.filterCandidates(valid,latest.candidates,"interest-12").map(item=>item.issue_id),[A]);
  assert.deepEqual(Interests.filterCandidates(valid,latest.candidates.filter(item=>item.topic_id==="risk"),"interest-12"),[]);
  assert.deepEqual(Interests.filterCandidates(valid,latest.candidates,""),latest.candidates);
  assert.deepEqual(Interests.filterCandidates(valid,latest.candidates,"interest-99"),[]);
  assert.equal(Interests.normalizeFilter(valid,"interest-12"),"interest-12");assert.equal(Interests.normalizeFilter(valid,"interest-99"),"");assert.equal(Interests.normalizeFilter(null,"interest-12"),"");
  assert.deepEqual(Interests.matchingInterests(valid,A).map(item=>item.id),["interest-12"]);assert.deepEqual(Interests.matchingInterests(valid,DART),[]);assert.deepEqual(latest.candidates,before);
});

// Exercise the real app handlers with a small DOM; no network, browser storage or provider call.
function appHarness() {
  const all=[], ids=new Map(), opened=[], fetched=[];
  class Node {
    constructor(tag="div",value="") { this.tagName=tag.toUpperCase();this.children=[];this.attrs={};this.dataset={};this.events={};this.className="";this.value=value;this.disabled=false;this.hidden=false;this.classList={toggle(){}};all.push(this); }
    set textContent(value) { this.children=[];this.text=String(value); }
    get textContent() { return (this.text||"")+this.children.map(child=>child.textContent).join(""); }
    set innerHTML(value) { throw new Error("HTML interpretation is forbidden in the interest renderer"); }
    append(...nodes) { this.children.push(...nodes);nodes.forEach(node=>node.parent=this); }
    prepend(...nodes) { this.children.unshift(...nodes);nodes.forEach(node=>node.parent=this); }
    replaceChildren(...nodes) { this.text="";this.children=[];this.append(...nodes);if(this.tagName==="SELECT"&&!this.children.some(node=>node.value===this.value))this.value=this.children[0]?.value||""; }
    get firstChild() { return this.children[0]; }
    remove() { if(this.parent)this.parent.children=this.parent.children.filter(node=>node!==this); }
    add(node) { this.append(node); }
    get options() { return this.children; }
    setAttribute(key,value) { this.attrs[key]=String(value); }
    getAttribute(key) { return this.attrs[key]; }
    addEventListener(type,callback) { (this.events[type] ||= []).push(callback); }
    fire(type) { this.events[type]?.forEach(callback=>callback({target:this})); }
    scrollIntoView() {}
  }
  const document={addEventListener(){},getElementById(id){if(!ids.has(id))ids.set(id,new Node(id.includes("filter")||id==="sort-order"?"select":"div"));return ids.get(id);},createElement:tag=>new Node(tag),createTextNode:text=>{const n=new Node("#text");n.textContent=text;return n;},querySelectorAll:selector=>all.filter(node=>selector.startsWith(".")&&node.className.split(" ").includes(selector.slice(1)))};
  const window={addEventListener(){},WeeklyInterests:Interests,WeeklyReview:Review,open:(...args)=>opened.push(args)};
  const context=vm.createContext({window,document,Option:function(label,value){const node=new Node("option",value);node.textContent=label;return node;},Intl,Date,URL,setTimeout:()=>0,clearTimeout(){},fetch:(...args)=>{fetched.push(args);throw new Error("Unexpected network");}});
  const source=fs.readFileSync(require.resolve("../site/app.js"),"utf8").replace(/  loadData\(\);\s*\}\)\(\);\s*$/, "  window.testApp = {state,interestsState,reviewState,initializeInterests,renderInterests,renderCandidates,renderMode,renderSourceStatus};\n})();");
  vm.runInContext(source,context);
  return {app:window.testApp,$:document.getElementById.bind(document),all,opened,fetched};
}
test("real panel renderer keeps injection text inert, badges matched news and preserves review drafts",() => {
  const {data,latest}=fixture(),marker='<img src=x onerror="alert(1)">';data.interests[0].title=marker;data.interests[0].question='<svg onload="alert(2)">';
  const h=appHarness();h.app.state.latest=latest;h.app.state.selected=A;const draft={choices:{[A]:"accepted"}};h.app.reviewState.draft=draft;
  h.app.initializeInterests({value:data});h.app.renderInterests();h.app.renderCandidates();
  assert(h.$("interest-cards").textContent.includes(marker));assert(h.$("candidate-list").textContent.includes(marker));assert.equal(h.all.filter(node=>["IMG","SVG","SCRIPT"].includes(node.tagName)).length,0);assert.equal(h.app.reviewState.draft,draft);
  assert.equal(h.$("interests-collect").disabled,false);assert.match(h.$("interests-status").textContent,/마지막 배포의 저장본/);
  h.$("interests-collect").fire("click");assert.deepEqual(h.opened,[[Interests.collectionURL(),"_blank","noopener,noreferrer"]]);assert.equal(h.fetched.length,0);
});
test("real filter/reset handlers restore all articles and stale selections disappear on registry refresh",() => {
  const {data,latest}=fixture(),h=appHarness();h.app.state.latest=latest;h.app.state.selected=A;h.app.initializeInterests({value:data});h.app.renderInterests();
  const filter=h.all.find(node=>node.className.includes("interest-filter-button"));filter.fire("click");assert.equal(h.$("interest-filter").value,"interest-12");assert.equal(h.$("candidate-list").children.length,1);
  h.$("reset-filters").fire("click");assert.equal(h.$("interest-filter").value,"");assert.equal(h.$("candidate-list").children.length,3);
  h.app.interestsState.filter="interest-12";h.app.renderInterests();assert.equal(h.$("interest-filter").value,"interest-12");data.interests=[];h.app.initializeInterests({value:data});h.app.renderInterests();assert.equal(h.$("interest-filter").value,"");assert.equal(h.$("interests-collect").disabled,false);
});
test("missing interest data renders a clear independent warning and leaves registration and manual request available",() => {
  const {latest}=fixture(),h=appHarness();h.app.state.latest=latest;h.app.reviewState.ready=true;h.app.initializeInterests({error:new Error("missing")});h.app.renderInterests();
  assert.equal(h.app.interestsState.data,null);assert.match(h.$("interests-status").textContent,/0건으로 판단하지 않습니다/);assert.equal(h.$("interest-filter").disabled,true);assert.equal(h.$("interests-collect").disabled,false);assert.equal(h.$("interests-register").href,Interests.registrationURL());assert.equal(h.$("interests-manage").href,Interests.managementURL());assert.equal(h.fetched.length,0);assert.equal(h.app.reviewState.ready,true);
});
test("interest-only raw runs are labeled separately and zero active interests mean skipped rather than missing source data",() => {
  for (const count of [0,1]) {
    const {latest}=fixture(),h=appHarness();h.app.state.latest=latest;h.app.state.status={schedule_enabled:true,interests_only:true,interests_count:count,sources:[],publication_status:"updated"};h.app.renderMode();h.app.renderSourceStatus();
    assert.equal(h.$("last-run-label").textContent,"마지막 관심사 별도 수집");assert.match(h.$("notice-description").textContent,/DART 공시는 이번 요청에서 갱신하지 않았습니다/);assert.match(h.$("source-status-note").textContent,/RSS 출처 상태/);
    if (!count) { assert.equal(h.$("source-status-summary").textContent,"활성 관심사 없어 수집 생략");assert.doesNotMatch(h.$("source-status-list").textContent,/출처별 수집 상태가 제공되지 않았습니다/); }
    else assert.match(h.$("source-status-summary").textContent,/관심사 RSS/);
  }
});
