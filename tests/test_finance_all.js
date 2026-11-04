"use strict";
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const Finance=require('../site/finance_all.js');
const STAMP='2026-11-04T06:26:00Z';
const copy=v=>JSON.parse(JSON.stringify(v));
function row(n=1,changes={}){return {article_id:'article-'+n.toString(16).padStart(20,'0'),version_id:'version-'+n.toString(16).padStart(20,'0'),title:'가상은행 고객 서비스 확대 '+n,source_id:'einfomax-s1n2',source_name:'연합인포맥스 · 증권',published_at:null,published_date:'2026-11-04',source_updated_at:null,fetched_at:STAMP,source_url:'https://news.einfomax.co.kr/news/articleView.html?idxno='+n,institutions:['가상은행'],...changes};}
function fixture(rows=[row()]){return {schema_version:'1.0',generation_id:'finance-'+'a'.repeat(20),generated_at:STAMP,is_demo:false,match_basis:'title_institution_name',count:rows.length,articles:rows};}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function harness({data=fixture(),hash='',fetchImpl}={}){
 const ids=new Map(),all=[],events=new Map(),requests=[],timers=[];
 class Node{
  constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.events={};this.attrs={};this.hidden=false;this.disabled=false;this.value='';this.className='';this.classes=new Set();this.classList={toggle:(name,on)=>{on?this.classes.add(name):this.classes.delete(name);}};all.push(this);}
  set textContent(v){this.text=String(v);this.children=[];}get textContent(){return(this.text||'')+this.children.map(n=>n.textContent).join('');}
  set innerHTML(_){throw Error('No HTML rendering');}
  append(...ns){this.children.push(...ns);}replaceChildren(...ns){this.text='';this.children=[...ns];}get firstChild(){return this.children[0];}get options(){return this.children;}
  setAttribute(k,v){this.attrs[k]=String(v);}addEventListener(k,fn){(this.events[k]||=[]).push(fn);}fire(k){for(const fn of this.events[k]||[])fn({target:this});}scrollIntoView(){}
 }
 for(const m of fs.readFileSync(require.resolve('../site/index.html'),'utf8').matchAll(/\bid="([^"]+)"/g))ids.set(m[1],new Node());
 ids.get('finance-sort').value='recent';
 const topics=[new Node('button'),new Node('button')],briefing=new Node(),review=new Node(),jump=new Node('a');
 const doc={getElementById:id=>ids.get(id)||null,createElement:tag=>new Node(tag),querySelectorAll:selector=>selector==='.topic-button'?topics:selector==='.briefing'?[briefing]:selector==='.review-workspace'?[review]:selector.startsWith('a[href=')?[jump]:[]};
 const win={location:{pathname:'/index.html',search:'',hash},history:{replaceState(a,b,url){win.location.hash=url.includes('#')?url.slice(url.indexOf('#')):'';}},CustomEvent:class{constructor(type,{detail}){this.type=type;this.detail=detail;}},addEventListener:(kind,fn)=>{(events.get(kind)||events.set(kind,[]).get(kind)).push(fn);},dispatchEvent:event=>{for(const fn of events.get(event.type)||[])fn(event);},AbortController,TextEncoder,TextDecoder,setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},fetch:async(path,options)=>{requests.push({path,options});if(fetchImpl)return fetchImpl(path,options,requests.length);return {ok:true,text:async()=>JSON.stringify(copy(data))};}};
 const modes=[];win.addEventListener('weekly:finance-view',e=>modes.push(e.detail.active));
 const controller=Finance.mount(win,doc);
 return {controller,win,ids,all,requests,briefing,review,topics,jump,modes,timers,$:id=>ids.get(id),async settle(){await controller.load();await tick();}};
}

test('raw finance schema accepts the four configured source endpoints and rejects summaries, DART and unsafe identities',()=>{
 const rows=[row(),row(2,{source_id:'einfomax-s1n7'}),row(3,{source_id:'newswire-securities',source_url:'https://www.newswire.co.kr/newsRead.php?no=3'}),row(4,{source_id:'fsc-press-releases',source_url:'https://www.fsc.go.kr/no010101/4'})];
 assert.equal(Finance.validate(fixture(rows)).count,4);
 for(const change of [x=>x.articles[0].summary='AI result',x=>x.articles[0].source_id='dart-company-1',x=>x.articles[0].source_url='javascript:alert(1)',x=>x.articles[0].source_url='https://news.einfomax.co.kr.evil.test/news/articleView.html?idxno=1',x=>x.articles[0].source_url+='&redirect=https://evil.test',x=>x.articles[0].source_url='https://user:secret@news.einfomax.co.kr/news/articleView.html?idxno=1',x=>x.count++,x=>x.articles[0].published_date='2026-02-30',x=>x.articles[0].fetched_at=null,x=>x.articles.push(copy(x.articles[0]))]){const v=fixture();change(v);assert.throws(()=>Finance.validate(v));}
});

test('search and institution filtering operate on raw titles, institutions and source without topic or AI membership',()=>{
 const data=Finance.validate(fixture([row(1,{title:'가상증권 일일 시황',institutions:['가상증권']}),row(2,{title:'가상은행 창구 안내',published_date:'2026-11-03'}),row(3,{title:'가상카드 고객 행사',institutions:['가상카드'],source_name:'보도자료'})]));
 assert.equal(Finance.filtered(data,{query:'시황'})[0].article_id,row(1).article_id);
 assert.equal(Finance.filtered(data,{query:'보도자료'}).length,1);
 assert.equal(Finance.filtered(data,{institution:'가상은행'}).length,1);
 assert.equal(Finance.filtered(data,{query:'없는단어'}).length,0);
 assert.equal(data.articles.length,3);
});

test('finance view reads only its saved JSON, hides review panels and returns without mutating their state',async()=>{
 const h=harness();await h.settle();h.review.savedDraft={accepted:'original'};
 h.$('finance-all-open').fire('click');assert.equal(h.controller.isActive(),true);assert.equal(h.$('finance-all').hidden,false);assert.equal(h.briefing.hidden,true);assert.equal(h.review.hidden,true);assert.equal(h.$('weekly-review').hidden,true);assert.equal(h.$('finance-all-open').attrs['aria-pressed'],'true');
 assert.match(h.$('finance-list').textContent,/가상은행/);assert.doesNotMatch(h.$('finance-list').textContent,/AI 요약|보고 후보|채택|미검토/);
 const anchor=h.all.find(n=>n.tagName==='A'&&n.href);assert.equal(anchor.target,'_blank');assert.equal(anchor.rel,'noopener noreferrer');
 h.topics[1].fire('click');assert.equal(h.controller.isActive(),false);assert.equal(h.briefing.hidden,false);assert.equal(h.review.hidden,false);assert.deepEqual(h.review.savedDraft,{accepted:'original'});assert.deepEqual(h.modes,[true,false]);
 assert.equal(h.requests.length,1);assert.equal(h.requests[0].path,'data/finance-all.json');assert.equal(h.requests[0].options.method,'GET');
});

test('finance list pagination renders fifty items and filters reset the current page',async()=>{
 const h=harness({data:fixture(Array.from({length:55},(_,i)=>row(i+1)))});await h.settle();assert.equal(h.$('finance-list').children.length,50);assert.equal(h.$('finance-page').textContent,'1 / 2');
 h.$('finance-next').fire('click');assert.equal(h.$('finance-list').children.length,5);assert.equal(h.$('finance-next').disabled,true);
 h.$('finance-search').value='확인되지않음';h.$('finance-search').fire('input');assert.equal(h.controller.state.page,0);assert.match(h.$('finance-list').textContent,/조건에 맞는/);assert.equal(h.$('finance-visible-count').textContent,'0건 / 전체 55건');
});

test('deep linking opens finance while failed and oversized requests show unknown rather than no financial news',async()=>{
 for(const response of [{ok:false,text:async()=>''},{ok:true,headers:{get:()=>String(5000000)},text:async()=>{throw Error('mustnotread');}},{ok:true,text:async()=>JSON.stringify({...fixture(),is_demo:true})}]){
  const h=harness({hash:'#finance-all',fetchImpl:async()=>response});await h.settle();assert.equal(h.controller.isActive(),true);assert.equal(h.$('finance-all-count').textContent,'—');assert.equal(h.$('finance-visible-count').textContent,'확인 불가');assert.match(h.$('finance-status').textContent,/불러오지 못/);assert.equal(h.requests.length,1);
 }
});

test('reload errors retain dated prior metadata and titles remain inert text',async()=>{
 const marker='<img src=x onerror=alert(1)>',data=fixture([row(1,{title:marker})]);
 const h=harness({data,fetchImpl:async(path,options,n)=>{if(n>1)throw Error('SECRET token provider response');return{ok:true,text:async()=>JSON.stringify(data)};}});await h.settle();await h.controller.load();assert.match(h.$('finance-status').textContent,/이전에 확인한/);assert(h.$('finance-list').textContent.includes(marker));assert.doesNotMatch(h.$('finance-status').textContent,/SECRET|token/);
});

test('HTML exposes a separate finance navigation button and keeps the canonical five-topic identities',()=>{
 const html=fs.readFileSync(require.resolve('../site/index.html'),'utf8');
 assert.match(html,/id="finance-all-open" class="finance-nav-button"/);assert.match(html,/finance_all\.js\?v=finance-all-v1/);
 assert.equal([...html.matchAll(/data-topic="([^"]+)"/g)].length,5);assert.match(html,/id="finance-all"[^>]+hidden/);
});


test('timestamp dates use Korea time and updated older articles sort by their new source update',async()=>{
 const changed=row(1,{published_at:'2026-11-03T16:00:00Z',published_date:'2026-11-03',source_updated_at:'2026-11-05T00:00:00Z'}),newer=row(2,{published_date:'2026-11-04'});
 const h=harness({data:fixture([newer,changed])});await h.settle();assert.match(h.$('finance-list').children[0].textContent,/2026.11.04/);assert(h.$('finance-list').children[0].textContent.includes(changed.title));
});

test('oversized streaming metadata is cancelled before rendering and slow reads reach a bounded error',async()=>{
 let cancelled=false;const streamed=harness({fetchImpl:async()=>({ok:true,body:{getReader:()=>({read:async()=>({done:false,value:new Uint8Array(4000001)}),cancel:async()=>{cancelled=true;},releaseLock(){}})}})});await streamed.settle();assert.equal(cancelled,true);assert.equal(streamed.$('finance-all-count').textContent,'—');
 const stalled=harness({fetchImpl:()=>new Promise(()=>{})});await tick();stalled.timers[0]();await stalled.controller.load();assert.equal(stalled.$('finance-visible-count').textContent,'확인 불가');assert.equal(stalled.requests[0].options.signal.aborted,true);
});

test('unknown publication dates are not represented as their collection date',async()=>{
 const h=harness({data:fixture([row(1,{published_at:null,published_date:null})])});await h.settle();assert.match(h.$('finance-list').textContent,/발행일 미확인 · 수집 2026\.11\.04/);
});
