import { Suspense } from 'react';
import { RequireAuth } from '@/components/AuthProvider';
import RecoveryDashboard from '@/components/RecoveryDashboard';

export const metadata = { title: 'Recover files - HDD File Recovery Suite' };

export default function DashboardPage() {
  return (
    <RequireAuth>
      <Suspense fallback={null}>
        <RecoveryDashboard />
      </Suspense>
    </RequireAuth>
  );
}
