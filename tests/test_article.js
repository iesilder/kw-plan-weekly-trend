"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const Article = require("../site/article.js");
const A="issue-"+"a".repeat(20),B="issue-"+"b".repeat(20),E1="version-"+"1".repeat(20),E2="version-"+"2".repeat(20);
const HASH="a".repeat(64),STAMP="2026-11-03T05:00:00Z",SOURCE="연합인포맥스(기사) · 증권",URL="https://news.einfomax.co.kr/news/articleView.html?idxno=1234567";
const clone=value=>JSON.parse(JSON.stringify(value));
function fixture(){
  const latest={schema_version:"1.0",generation_id:"live-test",is_demo:false,candidates:[
    {issue_id:A,is_demo:false,evidence_ids:[E1],source_name:SOURCE,content_filter:"topics"},
    {issue_id:B,is_demo:false,evidence_ids:[E2],source_name:"DART · 가상증권",content_filter:"earnings_only"}
  ]};
  const item={status:"pending",reason_code:null,evidence_ids:[E1],input_fingerprint:null,result_fingerprint:null,analyzed_at:null,source_url:URL,source_name:SOURCE,body_hash:null,body_fetched_at:null,truncated:false,result:null};
  const data={schema_version:"1.0",generation_id:latest.generation_id,generated_at:STAMP,provider:"google_gemini",model:"gemini-3.5-flash-lite",prompt_version:"article-brief-v1",auto_analysis_enabled:false,counts:{eligible:1,completed:0,stale:0,unavailable:0,pending:1,not_applicable:1,failed:0},last_attempt:null,items:{[A]:item,[B]:{...item,status:"not_applicable",reason_code:"not_article",evidence_ids:[E2],source_url:null,source_name:null}}};
  return {latest,data};
}
function complete(data){
  Object.assign(data.items[A],{status:"completed",reason_code:null,input_fingerprint:HASH,result_fingerprint:HASH,analyzed_at:STAMP,body_hash:"b".repeat(64),body_fetched_at:"2026-11-03T04:59:00Z",result:{key_change:{text:"가상증권이 새 기관 서비스를 출시했다.",evidence_ids:["p1"]},facts:[{text:"서비스는 법인 고객을 대상으로 한다.",evidence_ids:["p2"]}],stage:"출시·시행",why_candidate:"고객군과 사업 채널 변화라는 점에서 검토할 수 있다.",evidence:[{id:"p1",quote:"가상증권은 새 서비스를 출시했다."},{id:"p2",quote:"법인 고객을 대상으로 제공한다."}]}});
  data.counts.pending=0;data.counts.completed=1;
}
test("prepared state contains no body summary and does not claim actual analysis",()=>{
  const{data,latest}=fixture(),before=clone(data);const validated=Article.validateSidecar(data,latest);
  assert.equal(validated.counts.completed,0);assert.equal(validated.items[A].result,null);assert.equal(Article.summary(validated).lastAnalysisAt,null);assert.deepEqual(data,before);
});
test("completed article summary remains evidence and version bound",()=>{
  const{data,latest}=fixture();complete(data);const before=clone(latest);const validated=Article.validateSidecar(data,latest);
  assert.equal(validated.items[A].result.key_change.text,data.items[A].result.key_change.text);assert.equal(Article.summary(validated).label,"본문 요약 확인 가능");assert.equal(Article.summary(validated).lastAnalysisAt,STAMP);assert.deepEqual(latest,before);
});
test("wrong generation, identity, evidence IDs and forged counts reject entire sidecar",()=>{
  for(const change of [d=>d.generation_id="old",d=>delete d.items[B],d=>d.items[A].evidence_ids=[E2],d=>d.items[A].evidence_ids=[E1,E1],d=>d.counts.completed=1]){const{data,latest}=fixture();change(data);assert.throws(()=>Article.validateSidecar(data,latest));}
});
test("completed result requires matching source, dates and body/result fingerprints",()=>{
  for(const change of [d=>d.items[A].source_name="다른 언론사",d=>d.items[A].source_url="https://example.com/news",d=>d.items[A].input_fingerprint="c".repeat(64),d=>d.items[A].body_hash=null,d=>d.items[A].analyzed_at=null,d=>d.items[A].body_fetched_at="2026-02-30T00:00:00Z",d=>d.items[A].result=null,d=>d.items[A].reason_code="failed"]){const{data,latest}=fixture();complete(data);change(data);assert.throws(()=>Article.validateSidecar(data,latest));}
});
test("stale status may retain provenance but never returns old summary",()=>{
  const{data,latest}=fixture();complete(data);Object.assign(data.items[A],{status:"stale",input_fingerprint:null,result:null});data.counts.completed=0;data.counts.stale=1;
  const validated=Article.validateSidecar(data,latest);assert.equal(validated.items[A].result,null);assert.equal(Article.summary(validated).lastAnalysisAt,null);assert.match(Article.itemMessage(validated.items[A]),/이전 본문 요약을 표시하지 않습니다/);
  data.items[A].result={};assert.throws(()=>Article.validateSidecar(data,latest));
});
test("unavailable and failed body preserve metadata without invented brief",()=>{
  for(const status of ["unavailable","failed"]){const{data,latest}=fixture();data.items[A].status=status;data.items[A].reason_code=status==="unavailable"?"http_forbidden":"invalid_output";data.counts.pending=0;data.counts[status]=1;const validated=Article.validateSidecar(data,latest);assert.equal(validated.items[A].result,null);assert.equal(validated.counts.completed,0);assert.equal(Article.summary(validated).lastAnalysisAt,null);}
});
test("pending, unavailable, failed and nonarticle cannot carry completed result",()=>{
  for(const status of ["pending","unavailable","failed","not_applicable"]){const{data,latest}=fixture();complete(data);data.items[A].status=status;data.counts.completed=0;data.counts[status]++;if(status==="not_applicable")data.counts.eligible--;assert.throws(()=>Article.validateSidecar(data,latest));}
});
test("only configured current RSS source identities may be summarized",()=>{
  for(const change of [l=>l.candidates[0].is_demo=true,l=>l.candidates[0].source_name="다른 언론사",l=>l.candidates[0].content_filter="earnings_only",l=>l.is_demo=true]){const{data,latest}=fixture();change(latest);assert.throws(()=>Article.validateSidecar(data,latest));}
});
test("supported HTTPS article URLs normalize identity and reject unsafe formats",()=>{
  assert.equal(Article.articleURL(URL,SOURCE),URL);
  assert.equal(Article.articleURL(URL+"#body",SOURCE),URL);
  const newswire="https://www.newswire.co.kr/newsRead.php?no=1234567";
  assert.equal(Article.articleURL(newswire,"뉴스와이어(보도자료) · 증권"),newswire);
  for(const url of ["javascript:alert(1)",URL.replace("https:","http:"),URL.replace("news.einfomax.co.kr","news.einfomax.co.kr.evil.test"),URL.replace("news.einfomax.co.kr","user@news.einfomax.co.kr"),URL.replace("news.einfomax.co.kr","news.einfomax.co.kr:444"),URL.replace("articleView.html","articleList.html"),URL+"&idxno=7654321",URL+"&utm_source=feed",URL.replace("1234567","0"),URL.replace("1234567","abc"),"https://127.0.0.1/news/articleView.html?idxno=1234567",URL+"\n"]){assert.equal(Article.articleURL(url,SOURCE),null,url);}
});
test("detail binding checks source article identity as well as evidence version",()=>{
  const{data}=fixture();const item=data.items[A];const detail={evidence:[{evidence_id:E1,source_name:SOURCE,source_url:URL}]};
  assert.equal(Article.matchesDetail(item,detail),true);detail.evidence[0].source_url=URL.replace("1234567","1234568");assert.equal(Article.matchesDetail(item,detail),false);detail.evidence[0].source_url=URL;detail.evidence[0].evidence_id=E2;assert.equal(Article.matchesDetail(item,detail),false);
});
test("all claims reference existing exact evidence IDs and bounded unique quotes",()=>{
  for(const change of [r=>r.key_change.evidence_ids=["p9"],r=>r.facts[0].evidence_ids=[],r=>r.key_change.evidence_ids=["p1","p1"],r=>r.evidence.push({...r.evidence[0]}),r=>r.evidence[0].quote="x".repeat(81),r=>r.evidence=[{id:"p1",quote:"x".repeat(80)},{id:"p2",quote:"x".repeat(80)},{id:"p3",quote:"x".repeat(41)}],r=>r.evidence[0].id="p0",r=>r.stage="확정",r=>r.why_candidate="x".repeat(241),r=>r.key_change.text="x".repeat(261),r=>r.facts[0].text="x".repeat(181)]){const{data}=fixture();complete(data);change(data.items[A].result);assert.throws(()=>Article.validateResult(data.items[A].result));}
});
test("untrusted AI text remains text data and unknown body fields are never returned",()=>{
  const{data,latest}=fixture();complete(data);const marker='<img src=x onerror="alert(1)">';data.items[A].result.key_change.text=marker;data.items[A].result.full_body="not rendered";
  const validated=Article.validateSidecar(data,latest);assert.equal(validated.items[A].result.key_change.text,marker);assert.equal(Object.hasOwn(validated.items[A].result,"full_body"),false);
});
test("invalid attempts and automatic analysis cannot claim completed operation",()=>{
  const good={attempted_at:STAMP,completed_at:STAMP,status:"success",error_code:null,request_count:1,fetch_count:1,completed_count:1,cached_count:0,failed_count:0};
  for(const change of [d=>d.auto_analysis_enabled=true,d=>d.prompt_version="unrecognized",d=>d.model="different",d=>d.generated_at="yesterday",d=>d.last_attempt={...good,request_count:2},d=>d.last_attempt={...good,fetch_count:6},d=>d.last_attempt={...good,completed_at:"2026-11-02T05:00:00Z"}]){const{data,latest}=fixture();change(data);assert.throws(()=>Article.validateSidecar(data,latest));}
});
test("DART exclusion and truncated articles are described honestly",()=>{
  const{data}=fixture();assert.match(Article.itemMessage(data.items[B]),/공시 본문과 실적 수치 추출은 아직 연결하지 않았습니다/);complete(data);data.items[A].truncated=true;assert.match(Article.itemMessage(data.items[A]),/일부를 분석/);assert.match(Article.itemMessage({status:"unavailable",reason_code:"network_error"}),/제목 분석과 원문 링크/);
});
test("a large current cache does not count as a raised API batch",()=>{
  const{data,latest}=fixture();data.last_attempt={attempted_at:STAMP,completed_at:STAMP,status:"cached",error_code:null,request_count:0,fetch_count:0,completed_count:0,cached_count:20,failed_count:0};
  assert.doesNotThrow(()=>Article.validateSidecar(data,latest));assert.match(Article.itemMessage({status:"unavailable",reason_code:"access_restricted"}),/접근 제한/);
});
test("known article prompt versions remain compatible while unknown versions fail",()=>{
  for(const version of ["article-brief-v1","article-brief-v2"]){const{data,latest}=fixture();data.prompt_version=version;complete(data);assert.equal(Article.validateSidecar(data,latest).prompt_version,version);}
  const{data,latest}=fixture();data.prompt_version="article-brief-v3";assert.throws(()=>Article.validateSidecar(data,latest));
});
test("partial success retains four valid briefs and a separately failed item",()=>{
  const{data,latest}=fixture();data.prompt_version="article-brief-v2";complete(data);
  const extraIds=["c","d","e","f"].map(char=>"issue-"+char.repeat(20));
  for(const[index,id]of extraIds.entries()){
    const evidence="version-"+String(index+3).repeat(20);
    latest.candidates.push({...latest.candidates[0],issue_id:id,evidence_ids:[evidence]});
    data.items[id]={...clone(data.items[A]),evidence_ids:[evidence]};
  }
  Object.assign(data.items[extraIds[3]],{status:"failed",reason_code:"output_number_mismatch",result:null,result_fingerprint:null,analyzed_at:null});
  data.counts={eligible:5,completed:4,stale:0,unavailable:0,pending:0,not_applicable:1,failed:1};
  data.last_attempt={attempted_at:STAMP,completed_at:STAMP,status:"partial_success",error_code:null,request_count:1,fetch_count:5,completed_count:4,cached_count:0,failed_count:1};
  const before=clone(data);const validated=Article.validateSidecar(data,latest);
  assert.equal(validated.counts.completed,4);assert.equal(validated.counts.failed,1);assert.equal(validated.items[extraIds[3]].result,null);assert.equal(Article.summary(validated).label,"일부 분석 실패");
  for(const id of [A,...extraIds.slice(0,3)])assert.deepEqual(validated.items[id].result,before.items[id].result);
  assert.deepEqual(data,before);
});
test("item-specific output errors use safe explanatory messages without raw responses",()=>{
  for(const code of ["output_number_mismatch","output_evidence_mismatch","output_invalid_shape","output_missing_item","output_duplicate_item","output_quote_invalid"]){
    const message=Article.itemMessage({status:"failed",reason_code:code});assert.match(message,/이 자료|요약/);assert.doesNotMatch(message,new RegExp(code));
  }
  const marker="untrusted-provider-response-credential";assert.doesNotMatch(Article.itemMessage({status:"failed",reason_code:marker}),new RegExp(marker));
});
