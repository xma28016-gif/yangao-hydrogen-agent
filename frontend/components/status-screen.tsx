'use client';
import { useRouter } from 'next/navigation';
import { ArrowLeft, FileText, RotateCw } from 'lucide-react';
export function StatusScreen({ kind }: { kind: 'empty' | 'error' | 'not-found' }) {
  const router = useRouter();
  const titles = { empty: '还没有可阅读的报告', error: '暂时无法读取报告', 'not-found': '未找到这份报告' };
  const copy = { empty: '已有报告生成完成后，会出现在这个工作台。', error: '请确认本机研稿服务正在运行。已有资料和报告仍会保留。', 'not-found': '请检查报告地址，或返回查看已有报告。' };
  return <main id="main" className="status-screen"><div className="brand"><span className="brand-icon">研</span>研稿</div><FileText size={40} strokeWidth={1.2}/><h1>{titles[kind]}</h1><p role={kind === 'error' ? 'alert' : 'status'}>{copy[kind]}</p><button className="button primary" onClick={() => kind === 'not-found' ? router.push('/') : router.refresh()}>{kind === 'not-found' ? <ArrowLeft size={16}/> : <RotateCw size={16}/>} {kind === 'not-found' ? '返回工作台' : '重新读取'}</button></main>;
}
