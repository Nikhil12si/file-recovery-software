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
    const token = session.token();
    if (!token) {
      setReady(true);
      return;
    }
    if (cached) {
      setUser(cached);
    }
    setReady(true);

    if (typeof api.me === 'function') {
      api.me()
        .then((u) => {
          if (u) {
            setUser(u);
            session.save({ access_token: token, user: u });
          }
        })
        .catch((err) => {
          console.warn('api.me verification warning:', err);
          if (!session.user()) {
            signOutLocal();
          }
        })
        .finally(() => setReady(true));
    } else {
      setReady(true);
    }
  }, [router, signOutLocal]);

  const value = useMemo(() => ({
    user,
    ready,
    saveAuth(auth) {
      session.save(auth);
      setUser(auth.user);
      return auth.user;
    },
    async login(email, password, phoneNumber) {
      const res = await api.login(email, password, phoneNumber);
      if (res && res.two_factor_required) {
        return res;
      }
      session.save(res);
      setUser(res.user);
      return res.user;
    },
    async verify2FA(verificationId, code) {
      const auth = await api.verify2FA(verificationId, code);
      session.save(auth);
      setUser(auth.user);
      return auth.user;
    },
    async resend2FA(verificationId, phoneNumber) {
      return await api.resend2FA(verificationId, phoneNumber);
    },
    async register(email, password, fullName, phoneNumber) {
      const auth = await api.register(email, password, fullName, phoneNumber);
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
