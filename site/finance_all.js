/* Read-only financial news from stored raw metadata. No article-body or AI calls. */
(function(root,factory){
  "use strict";
  const api=factory();
  if(typeof module==="object"&&module.exports)module.exports=api;
  if(root){root.WeeklyFinanceAll=api;if(root.document)api.controller=api.mount(root,root.document);}
})(typeof window==="object"?window:null,function(){
  "use strict";
  const PAGE_SIZE=50,MAX_BYTES=4000000,MAX_RECORDS=10000;
  const SOURCES={"einfomax-s1n2":"einfomax","einfomax-s1n7":"einfomax","newswire-securities":"newswire","fsc-press-releases":"fsc"};
  const plain=v=>v!==null&&typeof v==="object"&&!Array.isArray(v);
  const keys=(value,expected)=>plain(value)&&Object.keys(value).length===expected.length&&expected.every(k=>Object.hasOwn(value,k));
  const text=(value,max)=>typeof value==="string"&&value.trim()===value&&value.length>0&&value.length<=max&&!/[\x00-\x1f\x7f]/.test(value);
  function validDate(value){if(value===null)return true;if(typeof value!=="string"||!/^\d{4}-\d{2}-\d{2}$/.test(value))return false;const at=new Date(value+"T00:00:00Z");return Number.isFinite(at.getTime())&&at.toISOString().slice(0,10)===value;}
  function validTime(value){return value===null||typeof value==="string"&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/.test(value)&&Number.isFinite(Date.parse(value))&&validDate(value.slice(0,10))&&+value.slice(11,13)<24&&+value.slice(14,16)<60&&+value.slice(17,19)<60;}
  function sourceURL(value,source){
    if(typeof value!=="string"||value.length>4000||/[\x00-\x20\x7f\\]/.test(value))throw Error("원문 링크를 확인할 수 없습니다.");
    let url;try{url=new URL(value);}catch{throw Error("원문 링크를 확인할 수 없습니다.");}
    if(url.protocol!=="https:"||url.username||url.password||url.port||url.hash)throw Error("원문 링크를 확인할 수 없습니다.");
    const kind=SOURCES[source],query=[...url.searchParams.entries()];
    const numeric=v=>typeof v==="string"&&/^[1-9]\d{0,11}$/.test(v);
    const ok=kind==="einfomax"&&url.hostname==="news.einfomax.co.kr"&&url.pathname==="/news/articleView.html"&&query.length===1&&query[0][0]==="idxno"&&numeric(query[0][1])||kind==="newswire"&&url.hostname==="www.newswire.co.kr"&&url.pathname==="/newsRead.php"&&query.length===1&&query[0][0]==="no"&&numeric(query[0][1])||kind==="fsc"&&url.hostname==="www.fsc.go.kr"&&/^\/no010101\/[1-9]\d{0,11}$/.test(url.pathname)&&query.length===0;
    if(!ok)throw Error("허용된 원문 출처가 아닙니다.");
    return url.href;
  }
  function validate(value){
    if(!keys(value,["schema_version","generation_id","generated_at","is_demo","match_basis","count","articles"])||value.schema_version!=="1.0"||value.is_demo!==false||value.match_basis!=="title_institution_name"||!/^finance-[a-f0-9]{20}$/.test(value.generation_id)||!validTime(value.generated_at)||value.generated_at===null||!Number.isSafeInteger(value.count)||value.count<0||value.count>MAX_RECORDS||!Array.isArray(value.articles)||value.articles.length!==value.count)throw Error("금융 전체 목록의 형식을 확인할 수 없습니다.");
    const seen=new Set();
    const articles=value.articles.map(row=>{
      if(!keys(row,["article_id","version_id","title","source_id","source_name","published_at","published_date","source_updated_at","fetched_at","source_url","institutions"])||!/^article-[a-f0-9]{20}$/.test(row.article_id)||seen.has(row.article_id)||!/^version-[a-f0-9]{20}$/.test(row.version_id)||!text(row.title,1000)||!Object.hasOwn(SOURCES,row.source_id)||!text(row.source_name,200)||!validTime(row.published_at)||!validDate(row.published_date)||!validTime(row.source_updated_at)||row.fetched_at===null||!validTime(row.fetched_at)||!Array.isArray(row.institutions)||row.institutions.length<1||row.institutions.length>100||!row.institutions.every(x=>text(x,100))||new Set(row.institutions).size!==row.institutions.length)throw Error("금융 전체 기사 정보를 확인할 수 없습니다.");
      seen.add(row.article_id);return {...row,source_url:sourceURL(row.source_url,row.source_id),institutions:[...row.institutions]};
    });
    return {...value,articles};
  }
  function filtered(data,{query="",institution="",sort="recent"}={}){
    const search=String(query).trim().toLocaleLowerCase("ko");
    const rows=data.articles.filter(row=>(!institution||row.institutions.includes(institution))&&(!search||[row.title,row.source_name,...row.institutions].join(" ").toLocaleLowerCase("ko").includes(search)));
    const when=row=>Math.max(Date.parse(row.published_at||row.published_date)||0,Date.parse(row.source_updated_at)||0)||Date.parse(row.fetched_at)||0;
    return rows.sort((a,b)=>sort==="title"?a.title.localeCompare(b.title,"ko"):when(b)-when(a)||a.title.localeCompare(b.title,"ko")||a.article_id.localeCompare(b.article_id));
  }
  async function readBounded(response,win){
    const declared=response.headers?.get?.("Content-Length");
    if(declared&&/^\d+$/.test(declared)&&Number(declared)>MAX_BYTES)throw Error("too_large");
    if(response.body?.getReader){
      const reader=response.body.getReader(),chunks=[];let size=0;
      try{while(true){const {value,done}=await reader.read();if(done)break;size+=value.byteLength;if(size>MAX_BYTES){await reader.cancel();throw Error("too_large");}chunks.push(value);}}
      finally{reader.releaseLock?.();}
      const all=new Uint8Array(size);let at=0;for(const chunk of chunks){all.set(chunk,at);at+=chunk.byteLength;}
      return new win.TextDecoder("utf-8",{fatal:true}).decode(all);
    }
    const raw=await response.text();if(new win.TextEncoder().encode(raw).byteLength>MAX_BYTES)throw Error("too_large");return raw;
  }
  function mount(win,doc){
    const $=id=>doc.getElementById(id),button=$("finance-all-open"),panel=$("finance-all");
    if(!button||!panel)return null;
    const state={active:false,data:null,page:0,loading:false,error:""};
    let pending=null;
    const node=(tag,cls,value)=>{const n=doc.createElement(tag);if(cls)n.className=cls;if(value!==undefined)n.textContent=String(value);return n;};
    const date=value=>{
      if(!value)return "날짜 미확인";
      if(/^\d{4}-\d{2}-\d{2}$/.test(value))return value.replaceAll("-",".");
      const parts=new Intl.DateTimeFormat("ko-KR",{timeZone:"Asia/Seoul",year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(new Date(value));
      const p=Object.fromEntries(parts.map(part=>[part.type,part.value]));return `${p.year}.${p.month}.${p.day}`;
    };
    const ordinary=()=>[...doc.querySelectorAll(".briefing"),...doc.querySelectorAll(".review-workspace"),$("weekly-review")].filter(Boolean);
    function notify(){if(typeof win.dispatchEvent==="function"&&typeof win.CustomEvent==="function")win.dispatchEvent(new win.CustomEvent("weekly:finance-view",{detail:{active:state.active}}));}
    function activate(active,{scroll=true}={}){
      state.active=active;panel.hidden=!active;button.classList.toggle("active",active);button.setAttribute("aria-pressed",String(active));
      ordinary().forEach(n=>{n.hidden=active;});
      if(active){doc.querySelectorAll(".topic-button").forEach(n=>{n.classList.toggle("active",false);n.setAttribute("aria-pressed","false");});if(win.location&&win.history?.replaceState)win.history.replaceState(null,"",`${win.location.pathname}${win.location.search}#finance-all`);}
      else if(win.location?.hash==="#finance-all"&&win.history?.replaceState)win.history.replaceState(null,"",`${win.location.pathname}${win.location.search}`);
      notify();if(active&&scroll)panel.scrollIntoView({behavior:"smooth",block:"start"});
    }
    function render(){
      $("finance-all-count").textContent=state.data?String(state.data.count):"—";
      $("finance-reload").disabled=state.loading;
      const list=$("finance-list");list.replaceChildren();
      if(!state.data){$("finance-visible-count").textContent=state.error?"확인 불가":"확인 중";$("finance-status").textContent=state.error||(state.loading?"저장된 금융 기사 목록을 확인하고 있습니다.":"저장된 목록을 확인해 주세요.");list.append(node("p","empty-state",state.error?"목록을 확인하지 못했습니다. 새로고침으로 다시 확인할 수 있습니다.":"목록을 불러오고 있습니다."));$("finance-prev").disabled=true;$("finance-next").disabled=true;$("finance-page").textContent="—";return;}
      const rows=filtered(state.data,{query:$("finance-search").value,institution:$("finance-institution").value,sort:$("finance-sort").value});
      const pages=Math.max(1,Math.ceil(rows.length/PAGE_SIZE));state.page=Math.min(state.page,pages-1);
      $("finance-visible-count").textContent=`${rows.length}건${rows.length!==state.data.count?` / 전체 ${state.data.count}건`:""}`;
      $("finance-status").textContent=(state.error?"새 목록 확인에 실패해 이전에 확인한 목록을 표시합니다. ":"")+`목록 생성 ${date(state.data.generated_at)} · 제목의 금융권 기관명 기준 · 원문 바로보기`;
      if(rows.length===0)list.append(node("p","empty-state","조건에 맞는 기사가 없습니다. 검색어나 기관 필터를 바꿔보세요."));
      for(const row of rows.slice(state.page*PAGE_SIZE,(state.page+1)*PAGE_SIZE)){
        const article=node("article","finance-record"),published=row.published_at||row.published_date;
        const meta=node("p","finance-source",`${row.source_name} · ${published?date(published):`발행일 미확인 · 수집 ${date(row.fetched_at)}`}`),heading=node("h3"),link=node("a","",row.title+" ↗");
        link.href=row.source_url;link.target="_blank";link.rel="noopener noreferrer";link.setAttribute("aria-label",`${row.title} · 원문 새 창으로 열기`);heading.append(link);
        const institutions=node("div","finance-institutions");institutions.setAttribute("aria-label","제목에 나타난 금융권 기관");row.institutions.forEach(name=>institutions.append(node("span","",name)));article.append(meta,heading,institutions);list.append(article);
      }
      $("finance-prev").disabled=state.page===0;$("finance-next").disabled=state.page+1>=pages;$("finance-page").textContent=`${state.page+1} / ${pages}`;
    }
    async function load(){
      if(pending)return pending;
      state.loading=true;state.error="";render();
      pending=(async()=>{
        const controller=new win.AbortController();let timer;
        const timeout=new Promise((_,reject)=>{timer=win.setTimeout(()=>{controller.abort();reject(Error("timeout"));},15000);});
        try{
          const request=(async()=>{
            const response=await win.fetch("data/finance-all.json",{method:"GET",credentials:"omit",cache:"no-store",redirect:"error",referrerPolicy:"no-referrer",signal:controller.signal});
            if(!response.ok)throw Error("request_failed");
            const raw=await readBounded(response,win);
            return validate(JSON.parse(raw));
          })();
          const next=await Promise.race([request,timeout]);const previous=$("finance-institution").value;
          state.data=next;const select=$("finance-institution");select.replaceChildren(node("option","","모든 기관"));select.firstChild.value="";
          [...new Set(next.articles.flatMap(row=>row.institutions))].sort((a,b)=>a.localeCompare(b,"ko")).forEach(name=>{const option=node("option","",name);option.value=name;select.append(option);});
          if([...select.options].some(n=>n.value===previous))select.value=previous;else select.value="";
        }catch{controller.abort();state.error="금융 전체 목록을 불러오지 못했습니다. 목록 새로고침으로 다시 확인해 주세요.";}
        finally{win.clearTimeout(timer);state.loading=false;pending=null;render();}
        return state.data;
      })();return pending;
    }
    button.addEventListener("click",()=>{activate(true);if(!state.data&&!state.loading)void load();});
    doc.querySelectorAll(".topic-button").forEach(n=>n.addEventListener("click",()=>{if(state.active)activate(false,{scroll:false});}));
    doc.querySelectorAll('a[href="#weekly-review"],a[href="#executive-interests"]').forEach(n=>n.addEventListener("click",()=>{if(state.active)activate(false,{scroll:false});}));
    $("finance-reload").addEventListener("click",()=>{void load();});
    $("reload-data")?.addEventListener("click",()=>{if(state.active)void load();});
    for(const id of ["finance-search","finance-institution","finance-sort"])$(id).addEventListener(id==="finance-search"?"input":"change",()=>{state.page=0;render();});
    $("finance-prev").addEventListener("click",()=>{if(state.page>0){state.page--;render();panel.scrollIntoView({behavior:"smooth",block:"start"});}});
    $("finance-next").addEventListener("click",()=>{if(state.data&&(state.page+1)*PAGE_SIZE<filtered(state.data,{query:$("finance-search").value,institution:$("finance-institution").value,sort:$("finance-sort").value}).length){state.page++;render();panel.scrollIntoView({behavior:"smooth",block:"start"});}});
    if(win.location?.hash==="#finance-all")activate(true,{scroll:false});
    win.addEventListener("hashchange",()=>{if(win.location?.hash==="#finance-all")activate(true,{scroll:false});else if(state.active)activate(false,{scroll:false});});
    void load();return {isActive:()=>state.active,activate,load,state};
  }
  return {validate,sourceURL,filtered,mount,PAGE_SIZE};
});
