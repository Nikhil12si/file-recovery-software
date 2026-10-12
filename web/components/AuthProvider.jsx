'use client';
/**
 * AuthProvider.jsx - client-side session state for Module 1 (Authentication).
 *
 * Persists the JWT + user in localStorage (survives reloads), revalidates it
 * with GET /api/auth/me on startup, and signs the user out automatically when
 * any API call returns 401 (expired or revoked token).
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { api, onUnauthorized, session } from '@/lib/api';
import { Spinner } from './ui';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(false);
  const router = useRouter();

  const signOutLocal = useCallback(() => {
    session.clear();
    setUser(null);
  }, []);

  useEffect(() => {
    onUnauthorized(() => {
      signOutLocal();
      router.replace('/login?expired=1');
    });
    const cached = session.user();
    if (!session.token()) {
      setReady(true);
      return;
    }
    setUser(cached);
    api.me()
      .then((u) => setUser(u))
      .catch(() => signOutLocal())
      .finally(() => setReady(true));
  }, [router, signOutLocal]);

  const value = useMemo(() => ({
    user,
    ready,
    async login(email, password) {
      const auth = await api.login(email, password);
      session.save(auth);
      setUser(auth.user);
      return auth.user;
    },
    async register(email, password, fullName) {
      const auth = await api.register(email, password, fullName);
      session.save(auth);
      setUser(auth.user);
      return auth.user;
    },
    async logout() {
      try { await api.logout(); } catch { /* token may already be invalid */ }
      signOutLocal();
      router.replace('/login');
    },
  }), [user, ready, router, signOutLocal]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}

/** Renders children only for signed-in users; otherwise redirects to /login. */
export function RequireAuth({ children }) {
  const { user, ready } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  useEffect(() => {
    if (ready && !user) router.replace(`/login?next=${encodeURIComponent(pathname || '/dashboard')}`);
  }, [ready, user, router, pathname]);
  if (!ready || !user) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center text-slate-500">
        <Spinner className="h-6 w-6" />
      </div>
    );
  }
  return children;
}
