import { RequireAuth } from '@/components/AuthProvider';
import SessionHistory from '@/components/SessionHistory';

export const metadata = { title: 'History & reports - HDD File Recovery Suite' };

export default function HistoryPage() {
  return (
    <RequireAuth>
      <SessionHistory />
    </RequireAuth>
  );
}
