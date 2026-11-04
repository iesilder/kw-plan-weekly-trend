/* Human-confirmed report history. Advisory matching only; no automatic exclusion. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;if(root)root.WeeklyHistory=api;})(typeof globalThis==='object'?globalThis:this,function(){
'use strict';
const ID=/^report-[a-f0-9]{32}$/;
function validate(store){
 if(!store||store.schema_version!=='1.0'||!Number.isSafeInteger(store.revision)||store.revision<0||!Array.isArray(store.reports)||!store.requests||typeof store.requests!=='object')throw Error('보고 이력 형식이 올바르지 않습니다.');
 const ids=new Set();for(const r of store.reports){if(!ID.test(r.id)||ids.has(r.id)||!Array.isArray(r.versions)||!r.versions.length)throw Error('보고 이력 식별 오류');ids.add(r.id);for(const v of r.versions){if(!['reported','cancelled'].includes(v.status)||typeof v.title!=='string'||typeof v.key_text!=='string'||typeof v.reported_on!=='string'||!Array.isArray(v.sources))throw Error('보고 기록 오류');}}
 return store;
}
function active(store){return validate(store).reports.map(r=>({id:r.id,...r.versions.at(-1),version_count:r.versions.length})).filter(r=>r.status==='reported');}
function url(value){try{const u=new URL(value);if(u.protocol!=='https:'||u.username||u.password)return '';u.hash='';for(const k of [...u.searchParams.keys()])if(/^utm_|^(fbclid|gclid)$/i.test(k))u.searchParams.delete(k);u.searchParams.sort();return u.href;}catch{return '';}}
function tokens(value){return new Set(String(value||'').toLowerCase().match(/[가-힣a-z0-9]{2,}/g)||[]);}
function overlap(a,b){const aa=tokens(a),bb=tokens(b);const shared=[...aa].filter(x=>bb.has(x));return {shared,score:shared.length/Math.max(1,Math.min(aa.size,bb.size))};}
function compare(candidate,store){
 const sources=Array.isArray(candidate.evidence)?candidate.evidence:[];
 const urls=new Set(sources.map(s=>url(s.source_url)).filter(Boolean));if(candidate.source_url)urls.add(url(candidate.source_url));
 return active(store).map(r=>{
  const same=r.sources.some(s=>s.issue_id===candidate.issue_id||(url(s.url)&&urls.has(url(s.url))));
  const sim=overlap(candidate.title,[r.title,r.key_text,...r.sources.map(s=>s.title)].join(' '));
  const company=!!candidate.company&&r.sources.some(s=>s.company===candidate.company);
  if(!same && !(sim.shared.length>=2&&sim.score>=0.35))return null;
  return {report:r,kind:same?'same_source':'possible_duplicate',label:same?'이미 보고에 사용한 자료':'기보고 내용과 중복 가능성',reason:same?'같은 자료 ID 또는 원문 URL이 연결돼 있습니다. 정정·후속 변화 여부는 직접 확인하세요.':`겹치는 제목 표현: ${sim.shared.slice(0,8).join(', ')}${company?' · 같은 기업':''}`,score:same?2:sim.score};
 }).filter(Boolean).sort((a,b)=>b.score-a.score||b.report.reported_on.localeCompare(a.report.reported_on)).slice(0,10);
}
return {validate,active,url,overlap,compare};
});
