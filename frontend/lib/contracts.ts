export type Report = { id: string; title: string; status: string; createdAt: number; targetWords: number };
export type Issue = { severity: 'CRITICAL' | 'WARNING'; quote: string; expected: string; source: string };
export type Chapter = {
  id: number; title: string; status: string; body: string; words: number; maxWords: number;
  missing: string[]; notice: string; truncated: boolean; edited: boolean; rewrites: number;
  issues: Issue[]; unverifiable: string[]; references: { filename: string; text: string }[];
};
export type Content = { status: string; words: number; notices: string[]; chapters: Chapter[] };

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('INVALID_RESPONSE');
  return value as Record<string, unknown>;
}
function text(value: unknown): string {
  if (typeof value !== 'string') throw new Error('INVALID_RESPONSE');
  return value;
}
function number(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) throw new Error('INVALID_RESPONSE');
  return value;
}
function array(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw new Error('INVALID_RESPONSE');
  return value;
}
function boolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new Error('INVALID_RESPONSE');
  return value;
}
export function parseReport(value: unknown): Report {
  const r = object(value);
  const id = text(r.id);
  if (!/^[a-f0-9]{32}$/.test(id)) throw new Error('INVALID_RESPONSE');
  return { id, title: text(r.title), status: text(r.status), createdAt: number(r.created_at), targetWords: number(r.target_words) };
}
export function parseReports(value: unknown): Report[] {
  return array(object(value).reports).map(parseReport);
}
export function parseContent(value: unknown): Content {
  const r = object(value);
  const chapters = array(r.chapters).map((value): Chapter => {
    const c = object(value), audit = object(c.audit);
    return {
      id: number(c.chapter_id), title: text(c.title), status: text(c.status), body: text(c.body),
      words: number(c.words), maxWords: number(c.max_words), missing: array(c.missing).map(text),
      notice: text(c.user_notice), truncated: boolean(c.truncated), edited: boolean(c.edited), rewrites: number(c.rewrite_count),
      issues: array(audit.issues).map((value) => {
        const i = object(value);
        if (i.severity !== 'CRITICAL' && i.severity !== 'WARNING') throw new Error('INVALID_RESPONSE');
        return { severity: i.severity, quote: text(i.quote), expected: text(i.expected), source: text(i.source) };
      }), unverifiable: array(audit.unverifiable).map(text),
      references: array(c.references).map(value => { const r = object(value); return { filename: text(r.filename), text: text(r.text) }; }),
    };
  });
  if (chapters.length > 30 || new Set(chapters.map(c => c.id)).size !== chapters.length
    || chapters.some(c => !Number.isInteger(c.id) || c.id < 1)) throw new Error('INVALID_RESPONSE');
  return { status: text(r.status), words: number(r.words), notices: array(r.notices).map(text), chapters };
}

export function missingMarkers(body: string): string[] {
  return [...new Set(body.match(/\[待补充[^\]\n]*\]/g) ?? [])];
}
export function chapterStatus(status: string): string {
  return ({ ready: '已定稿', needs_manual: '待人工', waiting: '等待中', writing: '写作中',
    compressing: '压缩中', auditing: '审计中', rewriting: '重写中' } as Record<string, string>)[status] ?? '状态待确认';
}

export type TextPart = { text: string; kind: 'plain' | 'missing' | 'critical' };
export function highlightParts(body: string, issues: Issue[]): TextPart[] {
  const matches: { start: number; end: number; kind: 'missing' | 'critical' }[] = [];
  for (const match of body.matchAll(/\[待补充[^\]\n]*\]/g)) {
    matches.push({ start: match.index, end: match.index + match[0].length, kind: 'missing' });
  }
  for (const i of issues.filter(i => i.severity === 'CRITICAL' && i.quote.length > 0)) {
    let at = 0;
    while ((at = body.indexOf(i.quote, at)) !== -1) {
      matches.push({ start: at, end: at + i.quote.length, kind: 'critical' }); at += i.quote.length;
    }
  }
  matches.sort((a, b) => a.start - b.start || b.end - a.end);
  const parts: TextPart[] = [];
  let end = 0;
  for (const match of matches) {
    if (match.start < end) continue;
    if (match.start > end) parts.push({ text: body.slice(end, match.start), kind: 'plain' });
    parts.push({ text: body.slice(match.start, match.end), kind: match.kind }); end = match.end;
  }
  if (end < body.length) parts.push({ text: body.slice(end), kind: 'plain' });
  return parts;
}
