import { getContent, getReport, getReports } from '@/lib/backend';
import { StatusScreen } from '@/components/status-screen';
import { Workbench } from '@/components/workbench';
export const dynamic = 'force-dynamic';
export default async function ReportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^[a-f0-9]{32}$/.test(id)) return <StatusScreen kind="not-found" />;
  let result;
  try {
    result = await Promise.all([getReport(id), getContent(id), getReports()]);
  } catch (error) {
    return <StatusScreen kind={error instanceof Error && error.message === 'NOT_FOUND' ? 'not-found' : 'error'} />;
  }
  const [report, content, reports] = result;
  return <Workbench key={id} report={report} content={content} reports={reports.filter(r => r.status === 'completed')} />;
}
