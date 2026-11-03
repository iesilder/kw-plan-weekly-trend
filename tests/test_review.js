"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const R = require("../site/review.js");
const A = "issue-" + "a".repeat(20), B = "issue-" + "b".repeat(20), C = "issue-" + "c".repeat(20);
const instant = "2026-11-03T01:00:00.000000+00:00";
const canonical = (revision = 1, decisions = {}) => ({ schema_version: "1.0", revision, updated_at: instant, decisions });
const decision = choice => ({ status: choice, updated_at: instant });
const article = (issueId, title, extra = {}) => ({ issue_id: issueId, title, company: "증권업권 관련", company_ids: [], companies: [], topic_id: "risk", topic: "정책·인허가·리스크", published_date: "2026-11-02", event_stage: "계획·추진", is_demo: false, source_name: "테스트 뉴스", ...extra });

test("canonical validates empty, archived and reset records without mutating inputs", () => {
  assert.deepEqual(R.validateCanonical(R.emptyCanonical()), R.emptyCanonical());
  const saved = canonical(2, { [A]: decision("accepted") });
  const copy = R.validateCanonical(saved);
  copy.decisions[A].status = "held";
  assert.equal(saved.decisions[A].status, "accepted");
  assert.deepEqual(R.validateCanonical(canonical(3)), canonical(3));
});

test("canonical rejects additional payload, naive/invalid dates, future choices and invalid revisions", () => {
  const bad = [
    { ...R.emptyCanonical(), note: "internal text" },
    { ...canonical(), updated_at: "2026-02-30T00:00:00Z" },
    { ...canonical(), updated_at: "2026-11-03T24:00:00Z" },
    { ...canonical(), updated_at: "2026-11-03T01:00:00" },
    { ...canonical(), revision: -1 },
    { ...canonical(), revision: 1.5 },
    { ...canonical(), revision: Number.MAX_SAFE_INTEGER + 1 },
    { ...R.emptyCanonical(), decisions: { [A]: decision("accepted") } },
    canonical(1, { [A]: { status: "unreviewed", updated_at: instant } }),
    canonical(1, { [A]: { status: "accepted", updated_at: "2026-11-04T00:00:00Z" } }),
    canonical(1, { [A]: { ...decision("accepted"), title: "should not upload" } })
  ];
  for (const value of bad) assert.throws(() => R.validateCanonical(value));
});

test("choices overlay saved values; reverting removes pending change; unreviewed resets", () => {
  const saved = canonical(1, { [A]: decision("accepted") });
  let draft = R.createDraft(saved);
  assert.equal(R.getStatus(saved, draft, B), "unreviewed");
  draft = R.setDecision(saved, draft, A, "held");
  assert.equal(R.getStatus(saved, draft, A), "held");
  draft = R.setDecision(saved, draft, A, "accepted");
  assert.deepEqual(draft.changes, {});
  draft = R.setDecision(saved, draft, A, "unreviewed");
  assert.equal(R.getStatus(saved, draft, A), "unreviewed");
  assert.equal(saved.decisions[A].status, "accepted");
});

test("patch and draft round trip exactly with active ID validation", () => {
  const saved = R.emptyCanonical();
  let draft = R.setDecision(saved, R.createDraft(saved), A, "accepted");
  draft = R.setDecision(saved, draft, B, "excluded");
  const patch = R.serializePatch(saved, draft);
  assert.deepEqual(R.draftFromPatch(R.validatePatch(JSON.parse(JSON.stringify(patch)), [A, B])), draft);
  assert.deepEqual(R.validateDraft(draft, new Set([A, B])), draft);
  assert.throws(() => R.validatePatch(patch, [A]));
  assert.throws(() => R.validateDraft(draft, [A]));
});

test("patch rejects arbitrary text/URL, duplicate IDs, invalid enums, prototype IDs, size over 200", () => {
  const good = { schema_version: "1.0", base_revision: 0, changes: [{ issue_id: A, status: "accepted" }] };
  const bad = [
    { ...good, comment: "private" },
    { ...good, changes: [{ ...good.changes[0], source_url: "https://example.com" }] },
    { ...good, changes: [good.changes[0], good.changes[0]] },
    { ...good, changes: [{ issue_id: A, status: "reported" }] },
    { ...good, changes: [{ issue_id: "__proto__", status: "accepted" }] },
    { ...good, changes: [{ issue_id: A, status: true }] },
    { ...good, base_revision: "0" },
    { ...good, schema_version: "2.0" },
    { ...good, changes: Array.from({ length: 201 }, (_, index) => ({ issue_id: "issue-" + index.toString(16).padStart(20, "0"), status: "held" })) }
  ];
  for (const value of bad) assert.throws(() => R.validatePatch(value));
});

test("saved updates never silently overwrite pending choices and acknowledge completed saves", () => {
  const before = R.emptyCanonical();
  const draft = R.setDecision(before, R.createDraft(before), A, "accepted");
  const otherUpdate = canonical(1, { [B]: decision("held") });
  assert.equal(R.hasConflict(otherUpdate, draft), true);
  assert.throws(() => R.setDecision(otherUpdate, draft, C, "accepted"));
  assert.throws(() => R.serializePatch(otherUpdate, draft));
  const conflict = R.reconcileDraft(otherUpdate, draft);
  assert.equal(conflict.conflict, true);
  assert.deepEqual(conflict.draft, draft);
  const afterSave = canonical(2, { [A]: decision("accepted") });
  assert.deepEqual(R.reconcileDraft(afterSave, draft), { draft: { base_revision: 2, changes: {} }, conflict: false, acknowledged: true });
  const rebased = R.rebaseDraft(otherUpdate, draft);
  assert.deepEqual(rebased, { base_revision: 1, changes: { [A]: "accepted" } });
});

test("partial save acknowledgment does not discard remaining conflicting choices", () => {
  const before = R.emptyCanonical();
  const draft = { base_revision: 0, changes: { [A]: "accepted", [B]: "held" } };
  const saved = canonical(1, { [A]: decision("accepted") });
  const result = R.reconcileDraft(saved, draft);
  assert.equal(result.conflict, true);
  assert.equal(Object.keys(result.draft.changes).length, 2);
});

test("KST day handles midnight, date-only and invalid/unknown dates without fetch fallback", () => {
  assert.equal(R.kstDate("2026-11-02T15:00:00Z"), "2026-11-03");
  assert.equal(R.kstDate("2026-11-02T14:59:59Z"), "2026-11-02");
  assert.equal(R.kstDate("2026-11-02"), "2026-11-02");
  assert.equal(R.publicationDate({ published_at: null, published_date: "2026-11-02" }), "2026-11-02");
  assert.equal(R.publicationDate({ published_date: "2026-10-26" }), "2026-10-26");
  assert.equal(R.publicationDate({}), null);
  assert.equal(R.publicationDate({ fetched_at: "2026-11-02T00:00:00Z" }), null);
  for (const value of [undefined, null, "0000-01-01", "2026-02-30", "2026-11-02T00:00:00", "2026-11-02T99:00:00Z", new Date("invalid")]) assert.equal(R.kstDate(value), null);
});

test("week boundaries cross years and publication range includes both endpoints", () => {
  assert.deepEqual(R.weekRange("2026-01-01"), { start: "2025-12-29", end: "2026-01-04" });
  assert.deepEqual(R.weekRange("2026-11-01T15:00:00Z"), { start: "2026-11-02", end: "2026-11-08" });
  assert.equal(R.inDateRange({ published_date: "2026-11-02" }, "2026-11-02", "2026-11-08"), true);
  assert.equal(R.inDateRange({ published_date: "2026-11-08" }, "2026-11-02", "2026-11-08"), true);
  assert.equal(R.inDateRange({ published_date: "2026-11-09" }, "2026-11-02", "2026-11-08"), false);
  assert.equal(R.inDateRange({}, "2026-11-02", "2026-11-08"), false);
  assert.equal(R.inDateRange({ published_date: "2026-11-02" }, "2026-11-08", "2026-11-02"), false);
  assert.throws(() => R.weekRange("2026-02-30"));
});

test("current T+1 original/follow-up becomes a symmetric duplicate candidate without mutating records", () => {
  const ids = ["issue-a99980f5c72b33d80750", "issue-a7a9b220314dfbd5acef"];
  const pair = [
    article(ids[0], "증권시장 결제주기 T+1 단축 내후년 도입…주요국보다 빠른 전환 추진"),
    article(ids[1], "증권 결제주기 T+1 단축 내후년…주요국보다 빠른 전환 추진(종합)")
  ];
  const before = JSON.stringify(pair);
  const map = R.findDuplicateCandidates(pair);
  assert.equal(map[ids[0]][0].issue_id, ids[1]);
  assert.equal(map[ids[1]][0].issue_id, ids[0]);
  assert.ok(map[ids[0]][0].score >= 0.7);
  assert.equal(JSON.stringify(pair), before);
  assert.deepEqual(R.findDuplicateCandidates([...pair].reverse()), map);
});

test("same company unrelated partnerships, different stages, numbers, old dates and routine filings are not grouped", () => {
  const common = { company: "가상증권", company_ids: ["company-1"], event_stage: "체결" };
  const pairs = [
    [article(A, "가상증권 ABC와 전략적 파트너십 체결", common), article(B, "가상증권 XYZ와 전략적 파트너십 체결", common)],
    [article(A, "가상증권 ABC와 글로벌 금융 상품 개발을 위한 전략적 파트너십 체결", common), article(B, "가상증권 XYZ와 글로벌 금융 상품 개발을 위한 전략적 파트너십 체결", common)],
    [article(A, "가상증권 하나은행과 글로벌 금융 상품 개발을 위한 전략적 파트너십 체결", common), article(B, "가상증권 가상은행과 글로벌 금융 상품 개발을 위한 전략적 파트너십 체결", common)],
    [article(A, "가상증권 토큰증권 금융 협력 인가 신청", { ...common, event_stage: "신청" }), article(B, "가상증권 토큰증권 금융 협력 인가 획득", { ...common, event_stage: "인가·승인" })],
    [article(A, "가상증권 장기 성장 사업 100억원 투자 추진", common), article(B, "가상증권 장기 성장 사업 200억원 투자 추진", common)],
    [article(A, "증권 결제주기 T+1 단축 내후년 전환 추진"), article(B, "증권 결제주기 T+1 단축 내후년 전환 추진", { published_date: "2026-10-01" })],
    [article(A, "연결재무제표기준영업(잠정)실적(공정공시)", { ...common, content_filter: "earnings_only" }), article(B, "연결재무제표기준영업(잠정)실적(공정공시)", { ...common, content_filter: "earnings_only" })],
    [article(A, "증권 결제주기 T+1 단축 내후년 전환 추진", { company_ids: ["company-1"] }), article(B, "증권 결제주기 T+1 단축 내후년 전환 추진", { company_ids: ["company-2"] })],
    [article(A, "증권 결제주기 T+1 단축 내후년 전환 추진", { published_date: null }), article(B, "증권 결제주기 T+1 단축 내후년 전환 추진")]
  ];
  for (const pair of pairs) assert.deepEqual(R.findDuplicateCandidates(pair), {});
});

test("exports group metadata with all original sources; never invent summaries or missing values", () => {
  const record = article(A, "새로운 서비스 출시", { company: "가상증권", topic: "신사업·상품·고객전략", evidence: [
    { source_name: "출처 1", published_date: "2026-11-02", source_url: "https://example.com/a" },
    { source_name: "출처 2", published_date: "2026-11-03", source_url: "https://example.org/b" }
  ] });
  const text = R.buildText([record], { start: "2026-11-02", end: "2026-11-08" });
  assert.match(text, /실제 보고 완료 이력이 아닙니다/);
  assert.match(text, /\[신사업·상품·고객전략\]\n가상증권\n- 새로운 서비스 출시/);
  assert.match(text, /https:\/\/example.com\/a/);
  assert.match(text, /https:\/\/example.org\/b/);
  const unknown = R.buildText([{ title: "날짜 없음" }]);
  assert.match(unknown, /발행일 미확인/);
  assert.match(unknown, /원문 링크 미확인/);
  assert.match(R.buildText([]), /채택 자료가 없습니다/);
});

test("exports reject unsafe links and CSV escapes quotes/newlines and formula prefixes", () => {
  for (const value of ["javascript:alert(1)", "http://example.com", "https://user:pass@example.com", "//example.com"]) assert.equal(R.safeSourceURL(value), null);
  const rows = [article(A, '=HYPERLINK("https://evil.test")\nother', { company: "  +cmd", evidence: [{ source_name: "@evil", source_url: "javascript:alert(1)" }] })];
  const csv = R.buildCSV(rows);
  assert.ok(csv.startsWith("\uFEFF"));
  assert.match(csv, /'  \+cmd/);
  assert.match(csv, /'=HYPERLINK\(""https:\/\/evil.test""\)/);
  assert.match(csv, /'@evil/);
  assert.doesNotMatch(csv, /javascript:/);
  assert.throws(() => R.buildCSV(rows, { start: "invalid", end: "2026-11-08" }));
});
