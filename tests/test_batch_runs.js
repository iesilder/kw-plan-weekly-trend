"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const Batch = require("../site/batch_runs.js");
const NOW = "2026-11-04T03:15:00.000Z";
const REPO = "iesilder/kw-plan-weekly-trend";
const API = `https://api.github.com/repos/${REPO}/actions/workflows/`;
const FILES = {daily:"daily.yml",pipeline:"pipeline.yml",interests:"interests.yml",rss:"rss.yml"};
const clone = value => JSON.parse(JSON.stringify(value));
function run(workflow = "daily", changes = {}) {
  return {id:workflow === "daily" ? 100 : workflow === "pipeline" ? 200 : 300,run_attempt:1,path:`.github/workflows/${FILES[workflow]}`,repository:{full_name:REPO},head_branch:"main",event:workflow === "daily" ? "schedule" : "workflow_dispatch",status:"completed",conclusion:"success",html_url:`https://github.com/${REPO}/actions/runs/${workflow === "daily" ? 100 : workflow === "pipeline" ? 200 : 300}`,created_at:"2026-11-04T02:01:00Z",run_started_at:"2026-11-04T02:01:01Z",updated_at:"2026-11-04T02:02:00Z",...changes};
}
function withId(record,id) { return {...record,id,html_url:`https://github.com/${REPO}/actions/runs/${id}`}; }
function reply(runs = [], {status = 200,total = runs.length,raw,headers = {}} = {}) {
  return {status,ok:status >= 200 && status < 300,headers:{get:name => headers[name.toLowerCase()] ?? null},text:async () => raw === undefined ? JSON.stringify({total_count:total,workflow_runs:runs}) : raw};
}
function setup(pages = {}, initial = NOW) {
  let time = Date.parse(initial), calls = [];
  const fetchImpl = async (url,options) => {
    calls.push({url,options});
    const file = new URL(url).pathname.split("/").at(-2), workflow = Object.keys(FILES).find(key => FILES[key] === file), page = Number(new URL(url).searchParams.get("page"));
    const entry = pages[workflow];
    if (typeof entry === "function") return entry({url,options,page});
    return reply(entry || []);
  };
  return {client:Batch.createClient({fetchImpl,now:() => time}),calls,advance:delta => { time += delta; },set:value => { time = Date.parse(value); }};
}

test("fixed public API requests return only bounded execution metadata and canonical attempt links",async () => {
  const fixture = setup({daily:[run("daily",{body:"SECRET",logs_url:"https://evil.test/raw",display_title:"<script>SECRET</script>"})]});
  const model = await fixture.client.load();
  assert.equal(model.records.length,1);
  assert.equal(model.records[0].url,`https://github.com/${REPO}/actions/runs/100/attempts/1`);
  assert.equal(model.records[0].state,"success");
  assert.equal(model.cached,false);
  assert.equal(fixture.calls.length,4);
  for (const {url,options} of fixture.calls) {
    assert.ok(url.startsWith(API));
    const parsed = new URL(url);
    assert.equal(parsed.searchParams.get("branch"),"main");
    assert.equal(parsed.searchParams.get("created"),">=2026-10-05T15:00:00.000Z");
    assert.equal(parsed.searchParams.get("per_page"),"100");
    assert.equal(options.credentials,"omit");
    assert.equal(options.redirect,"error");
    assert.equal(options.method,"GET");
    assert.ok(!options.body);
    assert.ok(!Object.keys(options.headers).some(name => name.toLowerCase() === "authorization"));
  }
  assert.doesNotMatch(JSON.stringify(model),/SECRET|logs_url|repository|head_commit|<script>/);
});

test("manual and interest success cannot stand in for an absent scheduled daily execution",async () => {
  const fixture = setup({daily:[run("daily",{event:"workflow_dispatch"})],pipeline:[run("pipeline")],interests:[run("interests",{event:"issues",display_title:"[관심사 수집 요청]"})]});
  const model = await fixture.client.load();
  assert.deepEqual(model.records.map(record => record.kind).sort(),["daily_manual","interest_manual","manual_collection"]);
  assert.equal(Batch.deriveToday(model,NOW).state,"no_record");
  assert.equal(Batch.deriveToday(model,NOW).record,null);
});

test("workflow failure includes deployment failure even if collection itself saved successfully",async () => {
  const fixture = setup({daily:[run("daily",{status:"completed",conclusion:"failure"})]});
  const today = Batch.deriveToday(await fixture.client.load(),NOW);
  assert.equal(today.state,"failure");
  assert.equal(today.record.kind,"daily_scheduled");
});

test("UI-only deployment and skipped Issues are omitted while registration rebuild is shown",async () => {
  const fixture = setup({interests:[run("interests",{event:"push"}),withId(run("interests",{event:"issues",display_title:"[관심사] 등록",status:"queued",conclusion:null}),301),withId(run("interests",{event:"issues",display_title:"[관심사 수집 요청]",conclusion:"skipped"}),302)]});
  const records = (await fixture.client.load()).records;
  assert.equal(records.length,1);
  assert.equal(records[0].kind,"interest_rebuild");
  assert.equal(records[0].state,"queued");
});

test("request issue titles can be generic without dropping a real workflow execution",async () => {
  const fixture = setup({interests:[run("interests",{event:"issues",display_title:"Collect executive interests and Pages"})]});
  assert.equal((await fixture.client.load()).records[0].kind,"interest_manual");
});

test("queued, running, cancellation, timeout and unknown conclusions remain distinct",async () => {
  for (const [status,conclusion,expected] of [["queued",null,"queued"],["requested",null,"queued"],["pending",null,"queued"],["waiting",null,"queued"],["in_progress",null,"running"],["completed","cancelled","cancelled"],["completed","timed_out","failure"],["completed","action_required","failure"],["completed","skipped","skipped"],["completed","neutral","unknown"]]) {
    const fixture = setup({daily:[run("daily",{status,conclusion})]});
    const model = await fixture.client.load();
    assert.equal(model.records[0].state,expected);
    assert.equal(model.records[0].startedAt,expected === "queued" ? null : "2026-11-04T02:01:01.000Z");
    assert.equal(Batch.deriveToday(model,NOW).state,expected);
  }
});

test("reruns retain attempt identity without treating yesterday's scheduled event as today's batch",async () => {
  const fixture = setup({daily:[run("daily",{run_attempt:2,created_at:"2026-11-03T02:00:00Z",run_started_at:"2026-11-04T02:10:00Z"})]});
  const model = await fixture.client.load();
  assert.equal(model.records[0].attempt,2);
  assert.match(model.records[0].url,/attempts\/2$/);
  assert.equal(Batch.deriveToday(model,NOW).state,"no_record");
});

test("KST calendar boundaries select scheduled events by creation day, not host UTC day",async () => {
  const fixture = setup({daily:[run("daily",{created_at:"2026-11-03T14:59:59Z"})]},"2026-11-03T15:00:00Z");
  const model = await fixture.client.load();
  const today = Batch.deriveToday(model,"2026-11-03T15:00:00Z");
  assert.equal(today.dateKst,"2026-11-04");
  assert.equal(today.scheduledAt,"2026-11-04T02:00:00.000Z");
  assert.equal(today.state,"waiting");
  assert.equal(today.record,null);
});

test("a pre-11:00 cached absence becomes unknown after 11:00 instead of no_record",async () => {
  const fixture = setup({},"2026-11-04T01:59:30Z");
  const model = await fixture.client.load();
  assert.equal(Batch.deriveToday(model,"2026-11-04T01:59:30Z").state,"waiting");
  assert.equal(Batch.deriveToday(model,"2026-11-04T02:00:00Z").state,"unknown");
});

test("no-record evidence expires and never moves to a new Korean day",async () => {
  const fixture = setup();
  const model = await fixture.client.load();
  assert.equal(Batch.deriveToday(model,NOW).state,"no_record");
  assert.equal(Batch.deriveToday(model,Date.parse(NOW) + 300001).state,"unknown");
  assert.equal(Batch.deriveToday(model,"2026-11-04T15:00:00Z").state,"unknown");
  assert.equal(Batch.deriveToday(null,NOW).state,"unknown");
});

test("daily API failure is unknown while independently loaded manual history remains visible",async () => {
  const fixture = setup({daily:() => reply([],{status:500,raw:"SECRET internal"}),interests:[run("interests")]});
  const model = await fixture.client.load();
  assert.equal(model.records[0].kind,"interest_manual");
  assert.equal(model.workflows.daily.complete,false);
  assert.equal(Batch.deriveToday(model,NOW).state,"unknown");
  assert.ok(model.warning);
  assert.doesNotMatch(JSON.stringify(model),/SECRET|internal/);
});

test("failed refresh keeps old dated records but does not reuse an old success as current proof",async () => {
  let fail = false;
  const fixture = setup({daily:() => fail ? reply([],{status:503}) : reply([run()])});
  const first = await fixture.client.load();
  fail = true; fixture.advance(60001);
  const second = await fixture.client.load({force:true});
  assert.equal(second.records[0].id,first.records[0].id);
  assert.equal(second.workflows.daily.checkedAt,first.workflows.daily.checkedAt);
  assert.equal(second.workflows.daily.complete,false);
  assert.equal(Batch.deriveToday(second,Date.parse(NOW)+60001).state,"unknown");
});

test("all pages are required before absence is inferred",async () => {
  const firstPage = Array.from({length:100},(_,index) => withId(run("daily",{event:"workflow_dispatch"}),index+1));
  const fixture = setup({daily:({page}) => reply(page === 1 ? firstPage : [withId(run(),101)],{total:101})});
  const model = await fixture.client.load();
  assert.equal(model.workflows.daily.complete,true);
  assert.equal(model.workflows.daily.records.length,101);
  assert.equal(Batch.deriveToday(model,NOW).state,"success");
  assert.equal(fixture.calls.filter(call => call.url.includes("daily.yml")).length,2);
});

test("pagination cap, changing totals and truncated pages cannot claim no scheduled record",async () => {
  const scenarios = [
    ({page}) => reply(Array.from({length:100},(_,index) => withId(run("daily",{event:"workflow_dispatch"}),(page-1)*100+index+1)),{total:301}),
    ({page}) => reply(page === 1 ? Array.from({length:100},(_,index) => withId(run("daily",{event:"workflow_dispatch"}),index+1)) : [],{total:page === 1 ? 101 : 100}),
    () => reply([run("daily",{event:"workflow_dispatch"})],{total:2})
  ];
  for (const entry of scenarios) {
    const fixture = setup({daily:entry});
    const model = await fixture.client.load();
    assert.equal(model.workflows.daily.complete,false);
    assert.equal(model.workflows.daily.error.code,"pagination_limit");
    assert.equal(Batch.deriveToday(model,NOW).state,"unknown");
  }
});

test("unsafe repository identities, paths, URLs, IDs, dates and repeated page rows fail closed",async () => {
  const bad = [{html_url:"https://evil.test/log"},{head_branch:"other"},{path:".github/workflows/untrusted.yml"},{repository:{full_name:"evil/repo"}},{id:-1},{run_attempt:0},{created_at:"2026-02-30T00:00:00Z"},{updated_at:"<script>"},{run_started_at:"2026-11-04T24:00:00Z"}];
  for (const changed of bad) {
    const fixture = setup({daily:[run("daily",changed)]});
    const model = await fixture.client.load();
    assert.equal(model.workflows.daily.complete,false);
    assert.equal(Batch.deriveToday(model,NOW).state,"unknown");
    assert.doesNotMatch(model.warning,/evil|<script>/);
  }
  const duplicate = setup({daily:[run(),run()]});
  assert.equal((await duplicate.client.load()).workflows.daily.complete,false);
});

test("inflight requests deduplicate, ordinary loads cache 5min, forced loads obey 60s cooldown",async () => {
  const fixture = setup({daily:[run()]});
  const [one,two] = await Promise.all([fixture.client.load(),fixture.client.load({force:true})]);
  assert.deepEqual(one,two);
  assert.equal(fixture.calls.length,4);
  one.records[0].state = "SECRET mutation";
  fixture.advance(59000);
  const cached = await fixture.client.load({force:true});
  assert.equal(cached.records[0].state,"success");
  assert.equal(cached.cached,true);
  assert.equal(fixture.calls.length,4);
  fixture.advance(1001);
  await fixture.client.load({force:true});
  assert.equal(fixture.calls.length,8);
  fixture.advance(299999);
  await fixture.client.load();
  assert.equal(fixture.calls.length,8);
  fixture.advance(1);
  await fixture.client.load();
  assert.equal(fixture.calls.length,12);
});

test("rate limit Retry-After prevents forced retries and preserves retry time without HTTP body",async () => {
  const fixture = setup({daily:() => reply([],{status:429,headers:{"retry-after":"600"},raw:"SECRET quota"})});
  const model = await fixture.client.load();
  assert.equal(model.workflows.daily.error.code,"rate_limited");
  assert.equal(model.workflows.daily.error.retryAt,"2026-11-04T03:25:00.000Z");
  fixture.advance(120000);
  const cached = await fixture.client.load({force:true});
  assert.equal(fixture.calls.length,4);
  assert.equal(Batch.deriveToday(cached,Date.parse(NOW)+120000).state,"unknown");
  assert.doesNotMatch(JSON.stringify(cached),/SECRET|quota/);
});

test("rate limit reset epoch and HTTP-date Retry-After are honored",async () => {
  for (const headers of [{"x-ratelimit-reset":String((Date.parse(NOW)+900000)/1000)},{"retry-after":"Wed, 04 Nov 2026 03:30:00 GMT"}]) {
    const fixture = setup({daily:() => reply([],{status:403,headers})});
    assert.equal((await fixture.client.load()).workflows.daily.error.retryAt,"2026-11-04T03:30:00.000Z");
  }
});

test("invalid JSON and oversized declared or streamed bodies are bounded and safe",async () => {
  let cancelled = false;
  const cases = [() => reply([],{raw:"SECRET invalid"}),() => reply([],{headers:{"content-length":"2000001"}}),() => ({status:200,ok:true,headers:{get:() => null},body:new ReadableStream({start(controller){controller.enqueue(new Uint8Array(2000001));},cancel(){cancelled=true;}})})];
  for (const response of cases) {
    const fixture = setup({daily:response});
    const model = await fixture.client.load();
    assert.equal(model.workflows.daily.complete,false);
    assert.equal(Batch.deriveToday(model,NOW).state,"unknown");
    assert.doesNotMatch(model.warning,/SECRET/);
  }
  assert.equal(cancelled,true);
});

test("lookup evidence uses request start so an API response arriving after 11:00 cannot prove absence",async () => {
  let at = Date.parse("2026-11-04T01:59:59Z");
  const client = Batch.createClient({now:() => at,fetchImpl:async () => { at = Date.parse("2026-11-04T02:00:01Z"); return reply([]); }});
  const model = await client.load();
  assert.equal(model.workflows.daily.checkedAt,"2026-11-04T01:59:59.000Z");
  assert.equal(Batch.deriveToday(model,at).state,"unknown");
});

test("known workflow ref suffixes are accepted without trusting arbitrary workflow locations",async () => {
  for (const suffix of ["@main","@refs/heads/main"]) {
    const fixture = setup({daily:[run("daily",{path:`.github/workflows/daily.yml${suffix}`})]});
    assert.equal((await fixture.client.load()).records[0].state,"success");
  }
});

test("a partial refresh supersedes a cached older attempt rather than retaining duplicate running rows",async () => {
  let partial = false;
  const fixture = setup({daily:() => partial ? reply([run("daily",{run_attempt:2})],{total:2}) : reply([run("daily",{status:"in_progress",conclusion:null})])});
  assert.equal((await fixture.client.load()).records[0].attempt,1);
  partial = true; fixture.advance(60001);
  const model = await fixture.client.load({force:true});
  assert.equal(model.workflows.daily.complete,false);
  assert.equal(model.records.length,1);
  assert.equal(model.records[0].attempt,2);
  assert.equal(model.records[0].state,"success");
});

test("history uses latest attempt execution time while today's schedule still uses the original event day",async () => {
  const fixture = setup({daily:[run("daily",{run_attempt:2,created_at:"2026-11-03T02:00:00Z",run_started_at:"2026-11-04T03:14:00Z",updated_at:"2026-11-04T03:14:59Z"})],interests:[run("interests",{created_at:"2026-11-04T03:00:00Z",run_started_at:"2026-11-04T03:00:01Z",updated_at:"2026-11-04T03:01:00Z"})]});
  const model = await fixture.client.load();
  assert.equal(model.records[0].id,100);
  assert.equal(model.records[0].attempt,2);
  assert.equal(model.records[1].id,300);
  assert.equal(Batch.deriveToday(model,NOW).state,"no_record");
});


test("RSS scheduled, manual and bootstrap saves stay separate from today's daily batch",async () => {
  const fixture = setup({rss:[withId(run("rss",{event:"schedule"}),400),withId(run("rss",{event:"workflow_dispatch"}),401),withId(run("rss",{event:"push"}),402)]});
  const model = await fixture.client.load();
  assert.deepEqual(model.records.map(record => record.kind).sort(),["rss_bootstrap","rss_manual","rss_scheduled"]);
  assert.equal(Batch.deriveToday(model,NOW).state,"no_record");
  assert.equal(Batch.deriveToday(model,NOW).record,null);
});

test("a full 30-day hourly RSS history is complete beyond the ordinary three-page limit",async () => {
  const fixture = setup({rss:({page}) => reply(Array.from({length:page < 8 ? 100 : 20},(_,index) => withId(run("rss",{event:"schedule"}),(page-1)*100+index+1000)),{total:720})});
  const model = await fixture.client.load();
  assert.equal(model.workflows.rss.complete,true);
  assert.equal(model.workflows.rss.records.length,720);
  assert.equal(fixture.calls.filter(call => call.url.includes("rss.yml")).length,8);
  assert.equal(model.warning,"");
});

test("hourly RSS history remains bounded and reports incompleteness rather than hiding truncation",async () => {
  const fixture = setup({rss:({page}) => reply(Array.from({length:100},(_,index) => withId(run("rss",{event:"schedule"}),(page-1)*100+index+1000)),{total:1001})});
  const model = await fixture.client.load();
  assert.equal(model.workflows.rss.complete,false);
  assert.equal(model.workflows.rss.error.code,"pagination_limit");
  assert.equal(fixture.calls.filter(call => call.url.includes("rss.yml")).length,10);
  assert.ok(model.warning);
  assert.equal(Batch.deriveToday(model,NOW).state,"no_record");
});


test("registry rename uses the explicit rebuild run label rather than implying source collection",async () => {
  const fixture = setup({interests:[run("interests",{event:"issues",display_title:"[관심사 재분류] 등록 내용 갱신"})]});
  const record = (await fixture.client.load()).records[0];
  assert.equal(record.kind,"interest_rebuild");
  assert.equal(record.label,"관심사 저장 자료 재분류");
});
