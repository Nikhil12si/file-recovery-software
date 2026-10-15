import './globals.css';
import { AuthProvider } from '@/components/AuthProvider';
import ProjectCreditHeader from '@/components/ProjectCreditHeader';
import NavBar from '@/components/NavBar';
import { AgentBanner } from '@/components/AgentStatus';

export const metadata = {
  title: 'HDD File Recovery Suite',
  description: 'Recover deleted files from Windows NTFS/FAT32 hard disk drives - Quick MFT scan and Deep sector carving.',
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `
              try {
                const theme = localStorage.getItem('theme');
                if (theme === 'light') {
                  document.documentElement.classList.remove('dark');
                } else {
                  document.documentElement.classList.add('dark');
                }
              } catch (_) {}
            `,
          }}
        />
      </head>
      <body className="min-h-screen bg-slate-100 text-slate-800 antialiased transition-colors duration-200 dark:bg-[#080d1a] dark:text-slate-100">
        <AuthProvider>
          <ProjectCreditHeader />
          <NavBar />
          <AgentBanner />
          <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
          <footer className="mx-auto max-w-7xl px-4 pb-12 pt-6 text-center text-xs text-slate-500 border-t border-slate-200/80 dark:border-slate-800/80 mt-12 space-y-2">
            <div className="text-slate-500 dark:text-slate-400">
              © 2026 HDD File Recovery Suite. All rights reserved.
            </div>
            <p className="text-slate-500 dark:text-slate-400 max-w-2xl mx-auto pt-1 leading-relaxed">
              Read-only recovery: source drives are opened with GENERIC_READ and recovered files are never written back to them.
            </p>
          </footer>
        </AuthProvider>
      </body>
    </html>
  );
}
