import UserManual from '@/components/UserManual';

export const metadata = {
  title: 'User Manual - HDD File Recovery Suite',
  description: 'Step-by-step guide: elevated start-up, choosing Quick vs Deep scans, safety rules and confidence ratings.',
};

// Public on purpose: users must be able to read the safety rules before signing in.
export default function ManualPage() {
  return <UserManual />;
}
