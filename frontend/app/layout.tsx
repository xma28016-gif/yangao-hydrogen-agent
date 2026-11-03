import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = { title: '研稿 · 报告工作台', description: '从项目资料到可核对的研究报告' };
export default function Layout({ children }: { children: React.ReactNode }) {
  return <html lang="zh-CN"><body><a className="skip-link" href="#main">跳到报告正文</a>{children}</body></html>;
}
