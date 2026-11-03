import 'server-only';
import { headers } from 'next/headers';
import { parseContent, parseReport, parseReports } from './contracts';
import { isLocalHost } from './local-boundary';

async function read(path: string): Promise<unknown> {
  if (!isLocalHost((await headers()).get('host'))) throw new Error('LOCAL_ONLY');
  const url = new URL(process.env.BACKEND_URL || 'http://127.0.0.1:18080');
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname)
    || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('BACKEND_CONFIG');
  const response = await fetch(new URL('/api/v1' + path, url), {
    method: 'GET', cache: 'no-store', signal: AbortSignal.timeout(10000), headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error(response.status === 404 ? 'NOT_FOUND' : 'BACKEND_UNAVAILABLE');
  return response.json();
}
export const getReports = async () => parseReports(await read('/reports'));
export const getReport = async (id: string) => parseReport(await read(`/reports/${id}`));
export const getContent = async (id: string) => parseContent(await read(`/reports/${id}/content`));
