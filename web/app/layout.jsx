import './globals.css';
import { AuthProvider } from '@/components/AuthProvider';
import NavBar from '@/components/NavBar';
import { AgentBanner } from '@/components/AgentStatus';

export const metadata = {
  title: 'HDD File Recovery Suite',
  description: 'Recover deleted files from Windows NTFS/FAT32 hard disk drives - Quick MFT scan and Deep sector carving.',
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>
          <NavBar />
          <AgentBanner />
          <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
          <footer className="mx-auto max-w-7xl px-4 pb-8 text-xs text-slate-400">
            Read-only recovery: source drives are opened with GENERIC_READ and recovered files are never written back to them.
          </footer>
        </AuthProvider>
      </body>
    </html>
  );
}
