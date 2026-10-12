import AgentSetup from '@/components/AgentSetup';

export const metadata = {
  title: 'Recovery Agent setup - HDD File Recovery Suite',
  description: 'Install and start the local Recovery Agent that scans and restores on your own PC.',
};

// Public on purpose: visitors need it before they can sign in (sign-in itself goes through the agent).
export default function AgentPage() {
  return <AgentSetup />;
}
