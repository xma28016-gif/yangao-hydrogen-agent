import { redirect } from 'next/navigation';
import { getReports } from '@/lib/backend';
import { StatusScreen } from '@/components/status-screen';
export const dynamic = 'force-dynamic';
export default async function Home() {
  let reports;
  try { reports = await getReports(); } catch { return <StatusScreen kind="error" />; }
  const ready = reports.find(r => r.status === 'completed');
  if (ready) redirect(`/reports/${ready.id}`);
  return <StatusScreen kind="empty" />;
}
