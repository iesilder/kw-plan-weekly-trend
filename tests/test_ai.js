"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const AI = require("../site/ai.js");
const A = "issue-"+"a".repeat(20), B="issue-"+"b".repeat(20), C="issue-"+"c".repeat(20);
const E1="version-"+"1".repeat(20),E2="version-"+"2".repeat(20),E3="version-"+"3".repeat(20);
function sample(){
  const latest={generation_id:"live-current",is_demo:false,candidates:[
    {issue_id:A,is_demo:false,source_name:"DART · 가상증권A",content_filter:"earnings_only",evidence_ids:[E1]},
    {issue_id:B,is_demo:false,source_name:"DART · 가상증권B",content_filter:"earnings_only",evidence_ids:[E2]},
    {issue_id:C,is_demo:false,source_name:"연합인포맥스(기사) · 증권",content_filter:"topics",evidence_ids:[E3]}
  ]};
  const pending=evidence=>({status:"not_analyzed",reason_code:null,input_fingerprint:"1".repeat(64),input_evidence_ids:[evidence],result_fingerprint:null,analyzed_at:null,error_code:null,result:null});
  const data={schema_version:"1.0",generation_id:latest.generation_id,generated_at:"2026-11-03T02:00:00Z",provider:"google_gemini",model:"gemini-3.5-flash-lite",prompt_version:"metadata-review-v1",policy_version:"dart-factual-metadata-v1",auto_analysis_enabled:false,last_attempt:null,counts:{eligible:2,blocked:1,completed:0,stale:0,failed:0,not_analyzed:2},items:{[A]:pending(E1),[B]:pending(E2),[C]:{status:"blocked",reason_code:"source_ai_prohibited",input_fingerprint:null,input_evidence_ids:[],result_fingerprint:null,analyzed_at:null,error_code:null,result:null}}};
  return{latest,data};
}
function complete(data,id=A){
  const item=data.items[id];item.status="completed";item.result_fingerprint=item.input_fingerprint;item.analyzed_at="2026-11-03T01:55:00Z";item.result={suggested_topic_id:"performance",relevance:"relevant",rationale:"실적 공시 서식이므로 실적 확인 후보입니다.",check_points:["원문에서 실제 실적 수치를 확인하세요."],duplicate_issue_ids:[]};data.counts.not_analyzed--;data.counts.completed++;
}
function attempt(error=null){return{attempted_at:"2026-11-03T01:54:00Z",completed_at:"2026-11-03T01:56:00Z",status:error?"blocked":"success",error_code:error,request_count:error?0:1,eligible_count:2,completed_count:error?0:1,cached_count:0,failed_count:0};}
function newsSample(){
  const fixture=sample();
  fixture.data.policy_version="public-metadata-inference-v2";
  fixture.data.prompt_version="metadata-review-v2";
  fixture.data.items[C]={...fixture.data.items[A],input_evidence_ids:[E3]};
  fixture.data.counts={eligible:3,blocked:0,completed:0,stale:0,failed:0,not_analyzed:3};
  return fixture;
}
test("prepare state does not claim real analysis or key readiness",()=>{const{data,latest}=sample();const validated=AI.validateSidecar(data,latest);assert.equal(validated.counts.completed,0);assert.equal(AI.summary(validated).actualAnalysisExists,false);assert.equal(AI.summary(validated).keyRequiredCount,null);assert.equal(AI.lastAnalysisAt(validated),null);assert.equal(AI.summary(validated).label,"수동 실행 준비");});
test("completed metadata advice remains separate and latest-version bound",()=>{const{data,latest}=sample();complete(data);data.last_attempt=attempt();const before=JSON.stringify(latest);const validated=AI.validateSidecar(data,latest);assert.equal(validated.items[A].result.suggested_topic_id,"performance");assert.equal(AI.summary(validated).actualAnalysisExists,true);assert.equal(AI.lastAnalysisAt(validated),data.items[A].analyzed_at);assert.equal(JSON.stringify(latest),before);});
test("missing key / free project confirmation never counts as completed",()=>{for(const code of ["missing_api_key","free_project_not_confirmed"]){const{data,latest}=sample();data.last_attempt=attempt(code);data.items[A].error_code=code;data.items[B].error_code=code;const v=AI.validateSidecar(data,latest);assert.equal(AI.keyRequiredCount(v),2);assert.equal(AI.summary(v).actualAnalysisExists,false);assert.equal(v.counts.completed,0);assert(AI.itemMessage(v.items[A]).includes("아직 분석하지 않았습니다"));}});
test("mismatched generation, identities, evidence, and forged count fail closed",()=>{for(const change of [(d,l)=>d.generation_id="old",(d,l)=>delete d.items[B],(d,l)=>d.items[A].input_evidence_ids=[E2],(d,l)=>d.counts.completed=1]){const{data,latest}=sample();change(data,latest);assert.throws(()=>AI.validateSidecar(data,latest));}});
test("blocked source cannot carry AI result or be marked completed",()=>{const{data,latest}=sample();data.items[C].result={suggested_topic_id:"risk"};assert.throws(()=>AI.validateSidecar(data,latest));const next=sample();next.data.items[C]={...next.data.items[A],input_evidence_ids:[E3]};next.data.counts.blocked=0;next.data.counts.eligible=3;next.data.counts.not_analyzed=3;assert.throws(()=>AI.validateSidecar(next.data,next.latest),/입력 범위/);});
test("stale analysis exposes state but never claims current completion",()=>{const{data,latest}=sample();complete(data);data.items[A].status="stale";data.items[A].input_fingerprint="2".repeat(64);data.counts.completed=0;data.counts.stale=1;const v=AI.validateSidecar(data,latest);assert.equal(AI.summary(v).label,"재분석 대상 있음");assert.equal(v.counts.completed,0);assert(AI.itemMessage(v.items[A]).includes("이전 제안은 표시하지 않으며"));});
test("completed result requires matching fingerprint, analysis time, and supported output",()=>{for(const change of [d=>d.items[A].result_fingerprint="9".repeat(64),d=>d.items[A].analyzed_at=null,d=>d.items[A].result.suggested_topic_id="invented",d=>d.items[A].result.relevance="high",d=>d.items[A].result.duplicate_issue_ids=[A]]){const{data,latest}=sample();complete(data);change(data);assert.throws(()=>AI.validateSidecar(data,latest));}});
test("AI output is preserved as text data, never interpreted HTML",()=>{const{data,latest}=sample();complete(data);const marker='<img src=x onerror="alert(1)">';data.items[A].result.rationale=marker;const v=AI.validateSidecar(data,latest);assert.equal(v.items[A].result.rationale,marker);assert.equal(Object.keys(v.items[A].result).length,5);});
test("invalid analysis and run dates are rejected",()=>{for(const change of [d=>d.generated_at="2026-02-30T00:00:00Z",d=>d.last_attempt={...attempt(),completed_at:"2026-11-02T00:00:00Z"},d=>d.auto_analysis_enabled=true]){const{data,latest}=sample();change(data);assert.throws(()=>AI.validateSidecar(data,latest));}});

test("completed duplicates only refer to current eligible DART sources",()=>{const{data,latest}=sample();complete(data);data.items[A].result.duplicate_issue_ids=[B];assert.doesNotThrow(()=>AI.validateSidecar(data,latest));data.items[A].result.duplicate_issue_ids=[C];assert.throws(()=>AI.validateSidecar(data,latest));});
test("hidden stale result may retain archived duplicate IDs and unchanged primary hash",()=>{const{data,latest}=sample();complete(data);data.items[A].status="stale";data.items[A].result.duplicate_issue_ids=["issue-"+"d".repeat(20)];data.counts.completed=0;data.counts.stale=1;const v=AI.validateSidecar(data,latest);assert.equal(v.items[A].status,"stale");assert.equal(v.counts.completed,0);});
test("public output bounds agree with backend schema",()=>{for(const change of [r=>r.rationale="x".repeat(301),r=>r.check_points=["a","b","c","d"],r=>r.check_points=["x".repeat(151)],r=>r.duplicate_issue_ids=Array(5).fill(B)]){const{data,latest}=sample();complete(data);change(data.items[A].result);assert.throws(()=>AI.validateSidecar(data,latest));}});
test("current metadata policy accepts all configured RSS titles without publisher blanket blocking",()=>{
  for(const source of ["연합인포맥스(기사) · 증권","연합인포맥스(기사) · IB·기업","뉴스와이어(보도자료) · 증권"]){
    const{data,latest}=newsSample();latest.candidates[2].source_name=source;latest.candidates[2].evidence_confidence="metadata_only";
    const before=JSON.stringify(latest);const validated=AI.validateSidecar(data,latest);
    assert.equal(validated.counts.eligible,3);assert.equal(validated.counts.blocked,0);assert.equal(validated.items[C].status,"not_analyzed");assert.equal(JSON.stringify(latest),before);
  }
});
test("news results and cross-source duplicate suggestions remain latest-evidence bound",()=>{
  const{data,latest}=newsSample();complete(data,C);data.items[C].result={suggested_topic_id:"risk",relevance:"relevant",rationale:"기사 제목에 정책 변화가 나타나 검토 후보입니다.",check_points:["원문에서 적용 대상과 시점을 확인하세요."],duplicate_issue_ids:[A]};
  complete(data,A);data.items[A].result.duplicate_issue_ids=[C];
  const validated=AI.validateSidecar(data,latest);assert.equal(validated.counts.completed,2);assert.deepEqual(validated.items[C].result.duplicate_issue_ids,[A]);
  data.items[C].input_evidence_ids=[E2];assert.throws(()=>AI.validateSidecar(data,latest),/근거 버전/);
});
test("unknown sources, unsupported policy and body-bearing records cannot masquerade as title inference",()=>{
  for(const change of [
    (d,l)=>l.candidates[2].source_name="알 수 없는 언론사",
    (d,l)=>l.candidates[2].content_filter="all_content",
    (d,l)=>l.candidates[2].evidence_confidence="full_text",
    (d,l)=>l.candidates[2].content_text="수집하지 않은 본문",
    (d,l)=>l.candidates[2].body="본문",
    (d,l)=>l.candidates[2].article_body="본문",
    (d,l)=>l.candidates[2].is_demo=true,
    (d,l)=>d.policy_version="unknown-policy"
  ]){const{data,latest}=newsSample();change(data,latest);assert.throws(()=>AI.validateSidecar(data,latest));}
});
test("news input is labeled as metadata rather than a body summary",()=>{
  const{data}=newsSample();assert.match(AI.itemMessage(data.items[C]),/본문은 입력에 포함되지 않습니다/);complete(data,C);
  assert.match(AI.itemMessage(data.items[C]),/기사·공시 본문을 읽거나 실적 수치를 분석한 결과가 아닙니다/);
});
