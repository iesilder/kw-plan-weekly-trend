const test=require('node:test'),assert=require('node:assert/strict'),H=require('../site/history.js');
const src={issue_id:'issue-'+'a'.repeat(20),title:'가상증권 인수 계약 체결',company:'가상증권',url:'https://example.com/a?idx=1'};
function store(status='reported'){return {schema_version:'1.0',revision:1,requests:{},reports:[{id:'report-'+'c'.repeat(32),versions:[{title:'가상증권 인수 계약',key_text:'가상증권의 인수 계약 체결을 전달',reported_on:'2026-01-02',status,sources:[src]}]}]};}
test('same id advisory even renamed title',()=>assert.equal(H.compare({issue_id:src.issue_id,title:'다른 제목'},store())[0].kind,'same_source'));
test('same canonical URL removes trackers but not article ids',()=>{assert.equal(H.compare({title:'별개',evidence:[{source_url:src.url+'&utm_source=mail#top'}]},store())[0].kind,'same_source');assert.notEqual(H.url(src.url),H.url('https://example.com/a?idx=2'));});
test('same firm different event not automatic duplicate',()=>assert.equal(H.compare({company:'가상증권',title:'가상증권 모바일 앱 출시'},store()).length,0));
test('similar titles yield advisory match',()=>assert.equal(H.compare({title:'가상증권 인수 계약 승인'},store())[0].kind,'possible_duplicate'));
test('cancelled is excluded and restored can compare',()=>{assert.equal(H.compare({issue_id:src.issue_id},store('cancelled')).length,0);const s=store('cancelled');s.reports[0].versions.push({...s.reports[0].versions[0],status:'reported'});assert.equal(H.active(s).length,1);});
test('empty means no history not no events',()=>assert.equal(H.compare({title:'가상'}, {schema_version:'1.0',revision:0,requests:{},reports:[]}).length,0));
test('unsafe URLs ignored',()=>{assert.equal(H.url('javascript:alert(1)'),'');assert.equal(H.url('https://user:pass@example.com'),'');});
test('bad store rejected',()=>assert.throws(()=>H.validate({reports:[]})));
test('comparison does not mutate source or store',()=>{const s=store(),old=JSON.stringify(s);H.compare({issue_id:src.issue_id},s);assert.equal(JSON.stringify(s),old);});
