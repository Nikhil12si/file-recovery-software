'use client';
/**
 * AuthForm.jsx - sign-in / registration form (Module 1 UI).
 */
import { useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useAuth } from './AuthProvider';
import { Alert, Icon, Spinner } from './ui';

export default function AuthForm() {
  const { login, register } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const [mode, setMode] = useState('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fullName, setFullName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const next = params.get('next') || '/dashboard';
  const passwordHint = mode === 'register' && password && (password.length < 8 || !/\d/.test(password) || !/[A-Za-z]/.test(password));

  async function submit(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === 'login') await login(email, password);
      else await register(email, password, fullName);
      router.replace(next.startsWith('/') ? next : '/dashboard');
    } catch (err) {
      setError(err.message || 'Something went wrong');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto mt-10 max-w-md">
      <div className="mb-6 text-center">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-brand-600 text-white"><Icon name="shield" className="h-6 w-6" /></div>
        <h1 className="mt-3 text-2xl font-bold text-slate-900">{mode === 'login' ? 'Sign in' : 'Create your account'}</h1>
        <p className="mt-1 text-sm text-slate-500">Windows HDD File Recovery Suite</p>
      </div>
      <form onSubmit={submit} className="card space-y-4 p-6">
        {params.get('expired') && <Alert tone="warning">Your session ended. Please sign in again.</Alert>}
        {error && <Alert tone="danger">{error}</Alert>}
        {mode === 'register' && (
          <div>
            <label className="label" htmlFor="name">Full name</label>
            <input id="name" className="input" value={fullName} onChange={(e) => setFullName(e.target.value)} autoComplete="name" />
          </div>
        )}
        <div>
          <label className="label" htmlFor="email">Email</label>
          <input id="email" type="email" required className="input" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
        </div>
        <div>
          <label className="label" htmlFor="password">Password</label>
          <input id="password" type="password" required className="input" value={password} onChange={(e) => setPassword(e.target.value)}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'} minLength={mode === 'register' ? 8 : undefined} />
          {passwordHint && <p className="mt-1 text-xs text-amber-700">Use at least 8 characters with letters and digits.</p>}
        </div>
        <button type="submit" className="btn-primary w-full" disabled={busy}>
          {busy && <Spinner />} {mode === 'login' ? 'Sign in' : 'Create account'}
        </button>
        <p className="text-center text-sm text-slate-600">
          {mode === 'login' ? 'New here?' : 'Already have an account?'}{' '}
          <button type="button" className="font-medium text-brand-600 hover:underline" onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError(null); }}>
            {mode === 'login' ? 'Create an account' : 'Sign in'}
          </button>
        </p>
        {mode === 'register' && <p className="text-center text-xs text-slate-500">The first account created becomes the administrator.</p>}
      </form>
      <p className="mt-4 text-center text-sm text-slate-500">
        First time? Read the <Link href="/manual" className="font-medium text-brand-600 hover:underline">User Manual</Link> before scanning.
      </p>
    </div>
  );
}
