import { describe, expect, it } from 'vitest';
import { chapterStatus, highlightParts, missingMarkers, parseContent, parseReport } from './contracts';

describe('不可信接口数据边界', () => {
  it('拒绝缺字段、错误类型和不安全ID，额外私有字段不传入UI', () => {
    expect(() => parseReport({ id: '../.env' })).toThrow();
    expect(() => parseReport({ id: 'a'.repeat(32), title: '标题', status: 'completed', created_at: '日期', target_words: 12000 })).toThrow();
    const value = parseReport({ id: 'a'.repeat(32), title: '标题', status: 'completed', created_at: 1, target_words: 12000, secret: 'not-for-client' });
    expect(value).not.toHaveProperty('secret');
  });
  it('拒绝重复章号，不用不确定的内容覆盖页面', () => {
    const ch = { chapter_id: 1, title: '概述', status: 'ready', body: '正文', words: 2, max_words: 100,
      missing: [], user_notice: '', truncated: false, edited: false, rewrite_count: 0,
      audit: { issues: [], unverifiable: [] }, references: [] };
    expect(() => parseContent({ status: 'completed', words: 4, notices: [], chapters: [ch, ch] })).toThrow();
    expect(parseContent({ status: 'completed', words: 2, notices: [], chapters: [ch] }).chapters[0].body).toBe('正文');
  });
  it('未来未知状态不会显示已定稿', () => expect(chapterStatus('new-future-status')).toBe('状态待确认'));
});
describe('正文与问题定位', () => {
  it('待补充按真实正文去重，空正文不造问题', () => {
    expect(missingMarkers('[待补充：环评]。\n[待补充：环评]。')).toEqual(['[待补充：环评]']);
    expect(missingMarkers('')).toEqual([]);
  });
  it('保留正文原字符且逐处标记CRITICAL，不改WARNING', () => {
    const body = '投资错误。未点名内容。[待补充：环评]。投资错误。';
    const parts = highlightParts(body, [
      { severity: 'CRITICAL', quote: '投资错误。', expected: '正确值', source: '资料.txt' },
      { severity: 'WARNING', quote: '未点名内容。', expected: '术语', source: '术语表' },
    ]);
    expect(parts.map(p => p.text).join('')).toBe(body);
    expect(parts.filter(p => p.kind === 'critical')).toHaveLength(2);
    expect(parts.filter(p => p.kind === 'missing')).toHaveLength(1);
  });
  it('重叠问题不重复正文，HTML内容仍只作为普通文本', () => {
    const body = '<script>不执行</script>[待补充：数据]';
    const parts = highlightParts(body, [{ severity: 'CRITICAL', quote: '[待补充：数据]', expected: '核对', source: '资料' }]);
    expect(parts.map(p => p.text).join('')).toBe(body);
    expect(parts[0]).toEqual({ kind: 'plain', text: '<script>不执行</script>' });
  });
});
