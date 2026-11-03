'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { ArrowDown, ArrowLeft, ArrowRight, BookOpen, ChevronDown, ChevronRight, CircleAlert,
  ClipboardList, FileCheck2, FileText, FolderOpen, List, LockKeyhole, PanelRightClose,
  PanelRightOpen, RotateCw, X } from 'lucide-react';
import { chapterStatus, highlightParts, missingMarkers, type Chapter, type Content, type Report } from '@/lib/contracts';

type TocProps = { chapters: Chapter[]; selected: number; onSelect: (id: number) => void };
function Contents({ chapters, selected, onSelect }: TocProps) {
  return <><div className="rail-heading"><span>报告目录</span><span>{chapters.length} 章</span></div>
    <nav className="toc" aria-label="章节目录">{chapters.map((chapter, i) =>
      <button key={chapter.id} aria-current={chapter.id === selected ? 'location' : undefined} onClick={() => onSelect(chapter.id)}>
        <span className="chapter-index">{String(i + 1).padStart(2, '0')}</span>
        <span className="toc-copy"><span>{chapter.title}</span><small className={chapter.status === 'needs_manual' ? 'danger-text' : ''}>{chapterStatus(chapter.status)}</small></span>
        {chapter.id === selected ? <ChevronRight size={14}/> : null}
      </button>)}</nav>
    <div className="toc-footnote"><FileCheck2 size={16}/><p>系统已完成的审计记录<br/>仍需结合资料人工核对</p></div></>;
}

function Problems({ content, chapter, onSelect, focused }: { content: Content; chapter: Chapter; onSelect: (id: number) => void; focused: string }) {
  const [scope, setScope] = useState<'chapter' | 'all'>('chapter');
  const rows = scope === 'all' ? content.chapters : [chapter];
  const count = rows.reduce((n, c) => n + missingMarkers(c.body).length, 0);
  return <><div className="rail-heading"><span>核对清单</span><ClipboardList size={16}/></div>
    <div className="scope-tabs" aria-label="问题范围"><button aria-pressed={scope === 'chapter'} onClick={() => setScope('chapter')}>当前章节</button><button aria-pressed={scope === 'all'} onClick={() => setScope('all')}>全文</button></div>
    <div className="problem-summary"><span className="small-square amber"><CircleAlert size={16}/></span><div><strong>{count} 项待补充</strong><p>补齐资料后，再核对对应内容</p></div></div>
    <div className="problem-scroll">{rows.map(c => {
      const missing = missingMarkers(c.body);
      if (!missing.length && !c.issues.length && !c.unverifiable.length) return null;
      return <section className="problem-group" key={c.id}>
        <button className="problem-chapter" onClick={() => onSelect(c.id)}>{String(c.id).padStart(2, '0')} · {c.title}<ChevronRight size={13}/></button>
        {c.edited ? <p className="quiet-note">本章已编辑，以下审计对应生成时的原稿。</p> : null}
        {missing.map((marker, i) => <div key={marker} className={`problem-row ${focused === marker ? 'focused-problem' : ''}`}><span className="problem-number">{String(i + 1).padStart(2, '0')}</span><p>{marker.replace(/^\[待补充[：:]?\s*|\]$/g, '')}</p></div>)}
        {c.issues.map((issue, i) => <div className={`audit-note ${issue.severity === 'CRITICAL' ? 'critical-note' : ''}`} key={`${issue.quote}-${i}`}><strong>{issue.severity === 'CRITICAL' ? '关键事实问题' : '术语提示'}</strong><p>原句：{issue.quote}</p><p>应为：{issue.expected}</p><small>依据：{issue.source}</small></div>)}
        {c.unverifiable.length ? <details className="unverifiable"><summary>资料无法核实的内容 · {c.unverifiable.length}</summary><ul>{c.unverifiable.map((item, i) => <li key={i}>{item}</li>)}</ul></details> : null}
      </section>;
    })}{!count && rows.every(c => !c.issues.length) ? <p className="empty-note">本范围没有记录到待补充或审计问题，仍建议对照资料阅读。</p> : null}</div>
    <div className="panel-legend"><span className="legend-dot missing-dot"/> 待补充 <span className="legend-dot critical-dot"/> 关键问题</div>
  </>;
}

function Drawer({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (open && dialog && !dialog.open) dialog.showModal();
    else if (!open && dialog?.open) dialog.close();
  }, [open]);
  return <dialog ref={ref} className="drawer" aria-label={title} onCancel={onClose} onClose={onClose} onClick={e => { if (e.target === e.currentTarget) onClose(); }}><div className="drawer-top"><strong>{title}</strong><button className="icon-button" onClick={onClose} aria-label={`关闭${title}`}><X size={20}/></button></div>{children}</dialog>;
}

export function Workbench({ report, content, reports }: { report: Report; content: Content; reports: Report[] }) {
  const router = useRouter(), search = useSearchParams();
  const [problemsVisible, setProblemsVisible] = useState(true);
  const [tocOpen, setTocOpen] = useState(false), [problemsOpen, setProblemsOpen] = useState(false);
  const [focused, setFocused] = useState('');
  const chapter = content.chapters.find(c => c.id === Number(search.get('chapter'))) ?? content.chapters[0];
  const missingTotal = content.chapters.reduce((n, c) => n + missingMarkers(c.body).length, 0);
  const manualCount = content.chapters.filter(c => c.status === 'needs_manual').length;
  const index = content.chapters.findIndex(c => c.id === chapter?.id);
  function selectChapter(id: number) {
    router.replace(`/reports/${report.id}?chapter=${id}`, { scroll: false });
    setTocOpen(false); setFocused('');
    document.getElementById('report-heading')?.scrollIntoView({ block: 'start', behavior: 'instant' });
  }
  function inspect(marker: string) {
    setFocused(marker); setProblemsVisible(true);
    if (window.matchMedia('(max-width: 1199px)').matches) setProblemsOpen(true);
  }
  const date = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(report.createdAt * 1000);
  return <div className="app">
    <header className="app-bar"><Link className="brand" href="/" prefetch={false} aria-label="研稿工作台"><span className="brand-icon">研</span><span>研稿<small>研究与写作</small></span></Link><div className="workspace-label"><span className="vertical-rule"/><FolderOpen size={15}/> 个人工作区 <ChevronRight size={13}/><span>报告工作台</span></div><span className="local-badge"><LockKeyhole size={13}/>本机资料</span></header>
    <main className="page" id="main">
      <div className="page-topline"><div className="breadcrumb"><FileText size={14}/><span>研究报告</span><ChevronRight size={13}/><span>阅读与核对</span></div><label className="report-select"><span>切换报告</span><select aria-label="切换报告" value={report.id} onChange={e => router.push(`/reports/${e.target.value}`)}>{reports.length ? reports.map(r => <option key={r.id} value={r.id}>{r.title} · {new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(r.createdAt * 1000)}</option>) : <option value={report.id}>{report.title}</option>}</select><ChevronDown size={13}/></label></div>
      <div className="report-header"><div><div className="eyebrow"><span className="status-dot"/>{content.status === 'completed' ? '报告已生成' : '报告阅读'}<span className="eyebrow-divider"/>创建于 {date}</div><h1>{report.title}</h1><p className="report-subtitle">阅读正文，核对待补充信息与审计问题。</p></div><button className="button primary desktop-issues" onClick={() => setProblemsVisible(v => !v)}>{problemsVisible ? <PanelRightClose size={17}/> : <PanelRightOpen size={17}/>} {problemsVisible ? '收起核对清单' : '展开核对清单'}</button><button className="button primary mobile-issues" onClick={() => setProblemsOpen(true)}><ClipboardList size={17}/>核对清单</button></div>
      <div className="report-summary"><span><BookOpen size={16}/><strong>{content.chapters.length}</strong> 章节</span><i/><span><FileText size={16}/><strong>{content.words.toLocaleString('zh-CN')}</strong> 字<span className="summary-hint">含标题与引用</span></span><i/><button onClick={() => { setProblemsVisible(true); if (window.matchMedia('(max-width:1199px)').matches) setProblemsOpen(true); }}><span className="legend-dot missing-dot"/><strong>{missingTotal}</strong> 项待补充</button><i/><span className={manualCount ? 'danger-text' : ''}><CircleAlert size={16}/><strong>{manualCount}</strong> 章待人工</span><span className="summary-end">现有报告 · 阅读预览</span></div>
      <div className={`workbench ${problemsVisible ? '' : 'without-problems'}`}>
        <aside className="contents-rail"><Contents chapters={content.chapters} selected={chapter?.id ?? 0} onSelect={selectChapter}/></aside>
        <div className="reading-column">
          <div className="reading-toolbar"><button className="button subtle mobile-toc" onClick={() => setTocOpen(true)}><List size={16}/>章节目录</button><span><BookOpen size={14}/>阅读模式</span><button className="icon-button" aria-label="重新读取报告" title="重新读取报告" onClick={() => router.refresh()}><RotateCw size={15}/></button></div>
          {chapter ? <article className="document" aria-labelledby="report-heading">
            <div className="document-eyebrow">第 {String(chapter.id).padStart(2, '0')} 章<span className={`chapter-badge ${chapter.status === 'needs_manual' ? 'manual-badge' : ''}`}>{chapterStatus(chapter.status)}</span></div>
            <h2 id="report-heading" tabIndex={-1}>{chapter.title}</h2>
            <div className="chapter-meta"><span>{chapter.words.toLocaleString('zh-CN')} 字</span><span>上限 {chapter.maxWords.toLocaleString('zh-CN')} 字</span>{chapter.rewrites ? <span>已重写 {chapter.rewrites} 次</span> : null}{chapter.edited ? <span>已人工编辑</span> : null}</div>
            <div className="document-rule"/>
            {chapter.body ? <div className="document-body">{highlightParts(chapter.body, chapter.edited ? [] : chapter.issues).map((part, i) => part.kind === 'plain' ? <span key={i}>{part.text}</span> : <button key={i} className={`inline-mark ${part.kind}`} onClick={() => inspect(part.text)} aria-label={`${part.text}，查看核对说明`}>{part.text}</button>)}</div> : <p className="empty-note">本章尚未形成正文。此阅读页不会自动开始生成。</p>}
            <div className="chapter-notes">{chapter.truncated ? <p><CircleAlert size={15}/>本章已按字数上限截断，请核对最后一段。</p> : null}{chapter.edited ? <p><FileCheck2 size={15}/>本章已人工修改。审计清单仍对应生成时的原稿。</p> : null}{chapter.notice ? <p>{chapter.notice}</p> : null}</div>
            <details className="references"><summary><span><FolderOpen size={16}/>本章参考资料<span className="count-tag">{chapter.references.length}</span></span><ChevronDown size={16}/></summary><p className="reference-hint">以下为本章写作时检索到的资料片段。</p>{chapter.references.map((reference, i) => <div className="reference" key={`${reference.filename}-${i}`}><h3><FileText size={14}/>{reference.filename}</h3><p>{reference.text}</p></div>)}</details>
            <div className="chapter-navigation"><button className="button secondary" disabled={index <= 0} onClick={() => selectChapter(content.chapters[index - 1].id)}><ArrowLeft size={15}/>上一章</button><span>{index + 1} / {content.chapters.length}</span><button className="button secondary" disabled={index >= content.chapters.length - 1} onClick={() => selectChapter(content.chapters[index + 1].id)}>下一章<ArrowRight size={15}/></button></div>
          </article> : <div className="document empty-note"><FileText size={28}/><h2>正文尚未生成</h2><p>已有内容保留在本机，阅读页不会自动发起生成。</p></div>}
        </div>
        {chapter && problemsVisible ? <aside className="problems-rail" aria-label="核对清单"><Problems content={content} chapter={chapter} onSelect={selectChapter} focused={focused}/></aside> : null}
      </div>
      {content.notices.length ? <details className="report-notices"><summary><CircleAlert size={15}/>报告提示 · {content.notices.length}<ChevronDown size={14}/></summary>{content.notices.map((notice, i) => <p key={i}>{notice}</p>)}</details> : null}
      <footer className="page-footer"><span>研稿<span className="footer-divider">/</span>生成内容请人工核对</span><a href="#main">返回顶部<ArrowDown size={12}/></a></footer>
    </main>
    <Drawer open={tocOpen} onClose={() => setTocOpen(false)} title="章节目录"><Contents chapters={content.chapters} selected={chapter?.id ?? 0} onSelect={selectChapter}/></Drawer>
    <Drawer open={problemsOpen} onClose={() => setProblemsOpen(false)} title="核对清单">{chapter ? <Problems content={content} chapter={chapter} onSelect={id => { selectChapter(id); setProblemsOpen(false); }} focused={focused}/> : <p className="empty-note">暂无可核对的正文。</p>}</Drawer>
  </div>;
}
