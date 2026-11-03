/* Review choices and advisory duplicate detection. No network calls or AI. */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.WeeklyReview = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";
  const MAX_CHANGES = 200;
  const ISSUE_ID = /^issue-[a-f0-9]{20}$/;
  const statusLabels = Object.freeze({ unreviewed: "미검토", accepted: "채택", held: "보류", excluded: "제외" });
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  const isObject = value => value !== null && typeof value === "object" && !Array.isArray(value) && [Object.prototype, null].includes(Object.getPrototypeOf(value));
  function fail(message) { throw new Error(message); }
  function keys(value, required, label) {
    if (!isObject(value) || Object.keys(value).sort().join("|") !== [...required].sort().join("|")) fail(`${label} 형식이 올바르지 않습니다.`);
  }
  function revision(value) { if (!Number.isSafeInteger(value) || value < 0) fail("검토 자료의 버전이 올바르지 않습니다."); return value; }
  function validDay(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000-")) return false;
    const parsed = new Date(`${value}T00:00:00.000Z`);
    return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
  }
  function validInstant(value) {
    if (typeof value !== "string") return false;
    const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$/.exec(value);
    if (!match || !validDay(match[1]) || +match[2] > 23 || +match[3] > 59 || +match[4] > 59) return false;
    if (match[5] !== "Z" && (+match[5].slice(1, 3) > 23 || +match[5].slice(4) > 59)) return false;
    return !Number.isNaN(new Date(value).getTime());
  }
  function id(value) { if (typeof value !== "string" || !ISSUE_ID.test(value)) fail("자료 ID가 올바르지 않습니다."); return value; }
  function status(value, allowUnreviewed = true) {
    if (typeof value !== "string" || !own(statusLabels, value) || (!allowUnreviewed && value === "unreviewed")) fail("검토 상태가 올바르지 않습니다.");
    return value;
  }
  function activeSet(activeIds) {
    if (activeIds === undefined || activeIds === null) return null;
    return activeIds instanceof Set ? activeIds : new Set(activeIds);
  }
  function emptyCanonical() { return { schema_version: "1.0", revision: 0, updated_at: null, decisions: {} }; }
  function validateCanonical(value) {
    keys(value, ["schema_version", "revision", "updated_at", "decisions"], "저장된 검토 자료");
    if (value.schema_version !== "1.0" || !isObject(value.decisions)) fail("지원하지 않는 검토 자료 형식입니다.");
    revision(value.revision);
    if (value.revision === 0 ? value.updated_at !== null || Object.keys(value.decisions).length !== 0 : !validInstant(value.updated_at)) fail("검토 자료의 저장 시각이 올바르지 않습니다.");
    const result = { schema_version: "1.0", revision: value.revision, updated_at: value.updated_at, decisions: {} };
    for (const [issueId, decision] of Object.entries(value.decisions)) {
      id(issueId);
      keys(decision, ["status", "updated_at"], "개별 검토 자료");
      status(decision.status, false);
      if (!validInstant(decision.updated_at) || new Date(decision.updated_at) > new Date(value.updated_at)) fail("개별 검토 자료의 저장 시각이 올바르지 않습니다.");
      result.decisions[issueId] = { status: decision.status, updated_at: decision.updated_at };
    }
    return result;
  }
  function createDraft(canonical) { return { base_revision: revision(canonical.revision), changes: {} }; }
  function validateDraft(value, activeIds) {
    keys(value, ["base_revision", "changes"], "임시 검토 자료");
    revision(value.base_revision);
    if (!isObject(value.changes) || Object.keys(value.changes).length > MAX_CHANGES) fail(`한 번에 ${MAX_CHANGES}건까지 검토 내용을 저장할 수 있습니다.`);
    const active = activeSet(activeIds);
    const result = { base_revision: value.base_revision, changes: {} };
    for (const [issueId, choice] of Object.entries(value.changes)) {
      id(issueId); status(choice);
      if (active && !active.has(issueId)) fail("현재 수집 목록에 없는 자료가 포함되어 있습니다.");
      result.changes[issueId] = choice;
    }
    return result;
  }
  function getStatus(canonical, draft, issueId) {
    if (draft && own(draft.changes, issueId)) return draft.changes[issueId];
    return own(canonical.decisions, issueId) ? canonical.decisions[issueId].status : "unreviewed";
  }
  function hasConflict(canonical, draft) { return Object.keys(draft.changes).length > 0 && draft.base_revision !== canonical.revision; }
  function setDecision(canonical, draft, issueId, choice) {
    id(issueId); status(choice);
    const result = validateDraft(draft);
    if (hasConflict(canonical, result)) fail("GitHub의 검토 자료가 변경되었습니다. 임시 선택을 확인한 뒤 다시 적용해주세요.");
    result.base_revision = revision(canonical.revision);
    const saved = own(canonical.decisions, issueId) ? canonical.decisions[issueId].status : "unreviewed";
    if (choice === saved) delete result.changes[issueId]; else result.changes[issueId] = choice;
    if (Object.keys(result.changes).length > MAX_CHANGES) fail(`한 번에 ${MAX_CHANGES}건까지 변경할 수 있습니다.`);
    return result;
  }
  function reconcileDraft(canonical, input) {
    const draft = validateDraft(input);
    const entries = Object.entries(draft.changes);
    const acknowledged = entries.length > 0 && entries.every(([issueId, choice]) => getStatus(canonical, null, issueId) === choice);
    if (!entries.length || acknowledged) return { draft: createDraft(canonical), conflict: false, acknowledged };
    return { draft, conflict: hasConflict(canonical, draft), acknowledged: false };
  }
  // Only call after the user explicitly chooses to reapply their pending choices.
  function rebaseDraft(canonical, input) {
    const draft = validateDraft(input);
    let result = createDraft(canonical);
    for (const [issueId, choice] of Object.entries(draft.changes)) result = setDecision(canonical, result, issueId, choice);
    return result;
  }
  function validatePatch(value, activeIds) {
    keys(value, ["schema_version", "base_revision", "changes"], "검토 저장 데이터");
    if (value.schema_version !== "1.0" || !Array.isArray(value.changes) || value.changes.length > MAX_CHANGES) fail("검토 저장 데이터 형식이나 건수를 확인해주세요.");
    revision(value.base_revision);
    const active = activeSet(activeIds);
    const seen = new Set();
    const changes = value.changes.map(change => {
      keys(change, ["issue_id", "status"], "검토 변경 내용");
      id(change.issue_id); status(change.status);
      if (seen.has(change.issue_id)) fail("같은 자료의 변경 내용이 두 번 포함되어 있습니다.");
      if (active && !active.has(change.issue_id)) fail("현재 수집 목록에 없는 자료가 포함되어 있습니다.");
      seen.add(change.issue_id);
      return { issue_id: change.issue_id, status: change.status };
    });
    return { schema_version: "1.0", base_revision: value.base_revision, changes };
  }
  function serializePatch(canonical, input) {
    const draft = validateDraft(input);
    if (hasConflict(canonical, draft)) fail("저장 버전이 달라 GitHub에 저장할 수 없습니다. 자료를 다시 확인해주세요.");
    return validatePatch({ schema_version: "1.0", base_revision: canonical.revision, changes: Object.keys(draft.changes).sort().map(issueId => ({ issue_id: issueId, status: draft.changes[issueId] })) });
  }
  function draftFromPatch(value) {
    const patch = validatePatch(value);
    return { base_revision: patch.base_revision, changes: Object.fromEntries(patch.changes.map(change => [change.issue_id, change.status])) };
  }
  function kstDate(value) {
    if (validDay(value)) return value;
    const parsed = value instanceof Date ? value : validInstant(value) ? new Date(value) : null;
    if (!parsed || Number.isNaN(parsed.getTime())) return null;
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(parsed).map(part => [part.type, part.value]));
    return `${parts.year}-${parts.month}-${parts.day}`;
  }
  function publicationDate(record) { return kstDate(record.published_at) || (validDay(record.published_date) ? record.published_date : null); }
  function weekRange(value = new Date()) {
    const day = kstDate(value);
    if (!day) fail("주간 범위의 기준일이 올바르지 않습니다.");
    const date = new Date(`${day}T00:00:00Z`);
    date.setUTCDate(date.getUTCDate() - (date.getUTCDay() + 6) % 7);
    const start = date.toISOString().slice(0, 10);
    date.setUTCDate(date.getUTCDate() + 6);
    return { start, end: date.toISOString().slice(0, 10) };
  }
  function inDateRange(record, start, end) {
    if (!validDay(start) || !validDay(end) || start > end) return false;
    const day = publicationDate(record);
    return day !== null && day >= start && day <= end;
  }
  function safeSourceURL(value) {
    if (typeof value !== "string" || !/^https:\/\//i.test(value)) return null;
    try { const url = new URL(value); return url.protocol === "https:" && url.hostname && !url.username && !url.password ? url.href : null; } catch { return null; }
  }
  const genericWords = new Set(["증권", "증권시장", "증권사", "사업", "추진", "추진한다", "확대", "강화", "체결", "협약", "업무협약", "전략적", "파트너십", "전략", "협력", "협업", "공동", "통한", "위한", "신규", "신설", "설립", "출시", "개시", "발표", "인수", "투자", "mou", "관련", "계획", "검토", "예정", "본격화"]);
  function titleTokens(record) {
    let value = String(record.title || "").normalize("NFKC").toLowerCase().replace(/[\[(](?:종합|상보|속보|수정|보완)[\])]/g, " ");
    const names = [record.company, ...(Array.isArray(record.companies) ? record.companies : [])].filter(Boolean).sort((a, b) => b.length - a.length);
    for (const name of names) value = value.split(String(name).normalize("NFKC").toLowerCase()).join(" ");
    return value.match(/[\p{L}\p{N}]+(?:\+[\p{N}]+)?/gu) || [];
  }
  function bigrams(value) { const result = new Set(); for (let i = 0; i < value.length - 1; i++) result.add(value.slice(i, i + 2)); return result; }
  function similarity(a, b) { const shared = [...a].filter(word => b.has(word)).length; return a.size + b.size ? shared / (a.size + b.size - shared) : 0; }
  function sameSubject(a, b) {
    const aIds = Array.isArray(a.company_ids) ? a.company_ids : [], bIds = Array.isArray(b.company_ids) ? b.company_ids : [];
    if (aIds.length || bIds.length) return aIds.length > 0 && bIds.length > 0 && [...aIds].sort().join("|") === [...bIds].sort().join("|");
    return typeof a.company === "string" && a.company.trim() !== "" && a.company === b.company;
  }
  function duplicateScore(a, b) {
    if (!a.issue_id || a.issue_id === b.issue_id || a.is_demo || b.is_demo || !a.topic_id || a.topic_id !== b.topic_id || !sameSubject(a, b)) return null;
    // Recurring legal form names do not establish that two filings are the same event.
    if ([a, b].some(record => record.content_filter === "earnings_only" || /^DART(?:\s|$)/i.test(record.source_name || ""))) return null;
    const aDay = publicationDate(a), bDay = publicationDate(b);
    if (!aDay || !bDay || Math.abs(new Date(aDay) - new Date(bDay)) > 3 * 86400000) return null;
    if ((a.event_stage || "") !== (b.event_stage || "")) return null;
    const aTokens = titleTokens(a), bTokens = titleTokens(b);
    const aNormal = aTokens.filter(token => !["증권", "증권시장"].includes(token)).join(""), bNormal = bTokens.filter(token => !["증권", "증권시장"].includes(token)).join("");
    if (aNormal.length < 12 || bNormal.length < 12) return null;
    const numbers = tokens => [...new Set(tokens.filter(token => /\d/.test(token)))].sort().join("|");
    if (numbers(aTokens) !== numbers(bTokens)) return null;
    // Distinct named counterparties must not disappear inside otherwise formulaic titles.
    const counterparties = tokens => [...new Set(tokens.filter(token => !genericWords.has(token) && (/[a-z]/.test(token) || /(?:증권|은행|재단|그룹|자산운용|코인)(?:과|와|의)?$/.test(token))))].sort().join("|");
    if (counterparties(aTokens) !== counterparties(bTokens)) return null;
    if (aNormal === bNormal) return 1;
    const aSet = new Set(aTokens.filter(token => !genericWords.has(token))), bSet = new Set(bTokens.filter(token => !genericWords.has(token)));
    const shared = [...aSet].filter(token => bSet.has(token));
    if (shared.length < 3 || shared.join("").length < 8) return null;
    const wordScore = similarity(aSet, bSet), gramScore = similarity(bigrams(aNormal), bigrams(bNormal));
    if (wordScore < 0.65 || gramScore < 0.70) return null;
    return Math.round((wordScore + gramScore) / 2 * 100) / 100;
  }
  function findDuplicateCandidates(records) {
    const result = {};
    const sorted = [...records].sort((a, b) => String(a.issue_id).localeCompare(String(b.issue_id)));
    for (let i = 0; i < sorted.length; i++) for (let j = i + 1; j < sorted.length; j++) {
      const score = duplicateScore(sorted[i], sorted[j]);
      if (score === null) continue;
      for (const [a, b] of [[sorted[i], sorted[j]], [sorted[j], sorted[i]]]) {
        (result[a.issue_id] ||= []).push({ issue_id: b.issue_id, score, reason: "같은 대상·주제·진행 단계이며 발행일과 제목이 유사합니다. 원문을 비교해주세요." });
      }
    }
    return result;
  }
  function sourceRows(record) {
    const inputs = Array.isArray(record.evidence) && record.evidence.length ? record.evidence : Array.isArray(record.sources) && record.sources.length ? record.sources : [record];
    const seen = new Set(), rows = [];
    for (const item of inputs) {
      const url = safeSourceURL(item.source_url || item.url);
      const source = String(item.source_name || item.source || record.source_name || "출처 미확인");
      const date = publicationDate(item) || publicationDate(record);
      const key = [source, date || "", url || ""].join("|");
      if (seen.has(key)) continue;
      seen.add(key); rows.push({ source, date, url });
    }
    return rows;
  }
  function sortedRecords(records) {
    return [...records].sort((a, b) => String(a.topic || a.topic_id || "미분류").localeCompare(String(b.topic || b.topic_id || "미분류"), "ko") || String(a.company || "대상 미확인").localeCompare(String(b.company || "대상 미확인"), "ko") || String(publicationDate(b) || "").localeCompare(String(publicationDate(a) || "")) || String(a.title || "").localeCompare(String(b.title || ""), "ko"));
  }
  function plain(value) { return String(value ?? "").replace(/[\u0000-\u001f\u007f]/g, " ").trim(); }
  function exportRange(range) {
    if (!range) return "";
    if (!validDay(range.start) || !validDay(range.end) || range.start > range.end) fail("내보내기 기간이 올바르지 않습니다.");
    return `${range.start} ~ ${range.end}`;
  }
  function buildText(records, range) {
    const period = exportRange(range);
    const lines = [`주간 보고 후보${period ? ` (${period})` : ""}`, "제목·출처 정보로 정리한 후보 목록입니다. 실제 보고 완료 이력이 아닙니다.", ""];
    let previousTopic = null, previousCompany = null;
    for (const record of sortedRecords(records)) {
      const topic = plain(record.topic || record.topic_id || "미분류"), company = plain(record.company || "대상 미확인");
      if (topic !== previousTopic) { if (previousTopic !== null) lines.push(""); lines.push(`[${topic}]`); previousTopic = topic; previousCompany = null; }
      if (company !== previousCompany) { lines.push(company); previousCompany = company; }
      lines.push(`- ${plain(record.title)}`);
      for (const row of sourceRows(record)) lines.push(`  ${row.date || "발행일 미확인"} | ${plain(row.source)} | ${row.url || "원문 링크 미확인"}`);
    }
    if (!records.length) lines.push("선택한 기간의 채택 자료가 없습니다.");
    return lines.join("\n");
  }
  function csvCell(value) {
    let text = String(value ?? "").replace(/\u0000/g, "");
    // Guard spreadsheet formula injection, including leading spaces/control characters.
    if (/^[\s\u0000-\u001f]*[=+\-@]/.test(text) || /^[\t\r\n]/.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  }
  function buildCSV(records, range) {
    exportRange(range);
    const rows = [["주제", "회사·대상", "제목", "발행일", "출처", "원문 링크", "검토 상태"]];
    for (const record of sortedRecords(records)) for (const row of sourceRows(record)) rows.push([record.topic || record.topic_id || "미분류", record.company || "대상 미확인", record.title || "", row.date || "발행일 미확인", row.source, row.url || "", "채택(보고 후보)"]);
    return "\uFEFF" + rows.map(row => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
  }
  return Object.freeze({ MAX_CHANGES, statusLabels, emptyCanonical, validateCanonical, createDraft, validateDraft, getStatus, setDecision, hasConflict, reconcileDraft, rebaseDraft, validatePatch, serializePatch, draftFromPatch, validDay, kstDate, publicationDate, weekRange, inDateRange, safeSourceURL, findDuplicateCandidates, buildText, buildCSV });
});
