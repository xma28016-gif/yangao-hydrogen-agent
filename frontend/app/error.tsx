'use client';
export default function ErrorPage({ reset }: { reset: () => void }) {
  return <main id="main" className="status-screen"><h1>页面暂时无法显示</h1><p>报告仍保存在本机，请重新读取。</p><button className="button primary" onClick={reset}>重新读取</button></main>;
}
