'use client';
/**
 * AuthForm.jsx - Live Twilio SMS 2-Step Verification & Sign-in.
 */
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useAuth } from './AuthProvider';
import { session } from '@/lib/api';
import { Alert, Icon, Spinner } from './ui';

export default function AuthForm() {
  const { register, saveAuth } = useAuth();
  const router = useRouter();
  const params = useSearchParams();

  const [mode, setMode] = useState('login'); // 'login' | 'register'
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fullName, setFullName] = useState('');
  const [phoneNumber, setPhoneNumber] = useState('');

  // 2-Step Verification state
  const [twoFactor, setTwoFactor] = useState(null); // null or { verificationId, phoneMasked, activePhone }
  const [otpCode, setOtpCode] = useState('');
  const [resendCooldown, setResendCooldown] = useState(0);
  const [resendSuccess, setResendSuccess] = useState(null);

  // Flexible phone number state (user can switch recipient on the fly)
  const [showChangePhone, setShowChangePhone] = useState(false);
  const [newRecipientPhone, setNewRecipientPhone] = useState('');

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const next = params.get('next') || '/dashboard';
  const passwordHint =
    mode === 'register' &&
    password &&
    (password.length < 8 || !/\d/.test(password) || !/[A-Za-z]/.test(password));

  // Cooldown countdown effect
  useEffect(() => {
    if (resendCooldown <= 0) return;
    const timer = setInterval(() => {
      setResendCooldown((c) => (c > 1 ? c - 1 : 0));
    }, 1000);
    return () => clearInterval(timer);
  }, [resendCooldown]);

  async function submit(e) {
    e.preventDefault();
    setError(null);
    setResendSuccess(null);
    setBusy(true);

    try {
      if (twoFactor) {
        // Step 2: Verify real Twilio OTP code
        if (!otpCode || otpCode.trim().length < 4) {
          setError('Please enter the 6-digit verification code you received on your phone.');
          setBusy(false);
          return;
        }

        const verifyRes = await fetch('/api/auth/verify-otp', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            verification_id: twoFactor.verificationId,
            code: otpCode.trim(),
          }),
        });

        const verifyData = await verifyRes.json();
        if (!verifyRes.ok) {
          throw new Error(verifyData.error || 'Invalid verification code. Please check your phone SMS.');
        }

        // Save authenticated session in AuthProvider state & localStorage
        saveAuth(verifyData);
        const dest = next && next.startsWith('/') ? next : '/dashboard';
        router.replace(dest);
        setTimeout(() => {
          if (window.location.pathname !== dest) {
            window.location.href = dest;
          }
        }, 150);
      } else if (mode === 'login') {
        // Step 1: Send real Twilio SMS to user's phone
        const targetPhone = phoneNumber.trim();
        if (!targetPhone) {
          setError('Please enter your phone number to receive the Twilio SMS verification code.');
          setBusy(false);
          return;
        }

        const sendRes = await fetch('/api/auth/send-otp', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            to_phone: targetPhone,
            email: email.trim(),
          }),
        });

        const sendData = await sendRes.json();
        if (!sendRes.ok) {
          throw new Error(sendData.error || 'Failed to send SMS via Twilio.');
        }

        setTwoFactor({
          verificationId: sendData.verification_id,
          phoneMasked: sendData.phone_masked,
          activePhone: targetPhone,
        });
        setResendCooldown(30);
      } else {
        // Register mode
        await register(email, password, fullName, phoneNumber.trim() || undefined);
        router.replace(next.startsWith('/') ? next : '/dashboard');
      }
    } catch (err) {
      setError(err.message || 'Something went wrong');
    } finally {
      setBusy(false);
    }
  }

  async function handleResendCode(customPhone = null) {
    if (!twoFactor || (resendCooldown > 0 && !customPhone) || busy) return;
    setError(null);
    setBusy(true);

    const targetPhone = customPhone || twoFactor.activePhone;

    try {
      const sendRes = await fetch('/api/auth/send-otp', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          to_phone: targetPhone,
          email: email.trim(),
        }),
      });

      const sendData = await sendRes.json();
      if (!sendRes.ok) {
        throw new Error(sendData.error || 'Failed to dispatch SMS via Twilio.');
      }

      setTwoFactor({
        verificationId: sendData.verification_id,
        phoneMasked: sendData.phone_masked,
        activePhone: targetPhone,
      });
      setResendSuccess(`Live verification code sent to ${sendData.phone_masked} via Twilio SMS.`);
      setResendCooldown(30);
      setShowChangePhone(false);
      setNewRecipientPhone('');
      setOtpCode('');
    } catch (err) {
      setError(err.message || 'Failed to dispatch verification code');
    } finally {
      setBusy(false);
    }
  }

  function handleCancel2FA() {
    setTwoFactor(null);
    setOtpCode('');
    setError(null);
    setResendSuccess(null);
    setShowChangePhone(false);
  }

  return (
    <div className="mx-auto mt-10 max-w-md">
      <div className="mb-6 text-center">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-brand-600 text-white shadow-md shadow-brand-500/20">
          <Icon name="shield" className="h-6 w-6" />
        </div>
        <h1 className="mt-3 text-2xl font-bold text-slate-900 dark:text-white">
          {twoFactor
            ? 'Two-Step Verification'
            : mode === 'login'
            ? 'Sign in'
            : 'Create your account'}
        </h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          {twoFactor
            ? 'Enter the 6-digit code sent to your phone via Twilio SMS'
            : 'Windows HDD File Recovery Suite'}
        </p>
      </div>

      <form onSubmit={submit} className="card space-y-4 p-6 shadow-sm border border-slate-200 dark:border-slate-800 dark:bg-slate-900/90">
        {params.get('expired') && !twoFactor && (
          <Alert tone="warning">Your session ended. Please sign in again.</Alert>
        )}
        {error && <Alert tone="danger">{error}</Alert>}
        {resendSuccess && <Alert tone="success">{resendSuccess}</Alert>}

        {twoFactor ? (
          /* ================= STEP 2: 2FA OTP SCREEN ================= */
          <div className="space-y-4">
            <div className="rounded-lg bg-slate-50 p-3.5 border border-slate-200 text-xs text-slate-600 space-y-1 dark:bg-slate-850 dark:border-slate-700/80 dark:text-slate-300">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5 font-medium text-slate-800 text-sm dark:text-slate-100">
                  <svg className="w-4 h-4 text-brand-600 dark:text-indigo-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z" />
                  </svg>
                  Twilio SMS Dispatched
                </div>
                <button
                  type="button"
                  onClick={() => setShowChangePhone(!showChangePhone)}
                  className="text-xs text-brand-600 hover:text-brand-700 font-medium hover:underline dark:text-indigo-400 dark:hover:text-indigo-300"
                >
                  {showChangePhone ? 'Cancel' : 'Change number'}
                </button>
              </div>
              <p>
                Verification code sent to{' '}
                <strong className="text-slate-900 font-mono dark:text-white">{twoFactor.phoneMasked}</strong>.
              </p>
            </div>

            {/* Change recipient phone number on the fly */}
            {showChangePhone && (
              <div className="rounded-lg bg-slate-100 p-3 border border-slate-300 space-y-2 text-xs dark:bg-slate-800/90 dark:border-slate-700">
                <label className="label text-slate-700 font-semibold dark:text-slate-200" htmlFor="new-phone">
                  Send code to a different phone number
                </label>
                <div className="flex gap-2">
                  <input
                    id="new-phone"
                    type="tel"
                    placeholder="+91 98765 43210"
                    className="input font-mono text-xs py-1.5 flex-1"
                    value={newRecipientPhone}
                    onChange={(e) => setNewRecipientPhone(e.target.value)}
                  />
                  <button
                    type="button"
                    disabled={!newRecipientPhone.trim() || busy}
                    onClick={() => handleResendCode(newRecipientPhone.trim())}
                    className="btn-primary text-xs px-3 py-1.5 shrink-0"
                  >
                    Send SMS
                  </button>
                </div>
                <p className="text-[11px] text-slate-500 dark:text-slate-400">
                  Include country code (e.g. +91 or +1). Twilio will deliver the SMS immediately.
                </p>
              </div>
            )}

            <div>
              <label className="label text-center dark:text-slate-200" htmlFor="otp">
                Enter 6-Digit Verification Code
              </label>
              <input
                id="otp"
                type="text"
                maxLength={6}
                required
                autoFocus
                placeholder="••••••"
                className="input text-center font-mono text-2xl tracking-[0.35em] font-bold py-2.5 text-slate-900 placeholder:text-slate-300 dark:text-white dark:placeholder:text-slate-600"
                value={otpCode}
                onChange={(e) => setOtpCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                autoComplete="one-time-code"
              />
            </div>

            <button type="submit" className="btn-primary w-full py-2.5 text-base font-semibold" disabled={busy}>
              {busy && <Spinner />} Verify & Continue
            </button>

            <div className="flex items-center justify-between text-xs pt-1">
              <button
                type="button"
                onClick={handleCancel2FA}
                className="text-slate-500 hover:text-slate-800 font-medium dark:text-slate-400 dark:hover:text-slate-200"
              >
                ← Back to sign in
              </button>
              <button
                type="button"
                onClick={() => handleResendCode(null)}
                disabled={resendCooldown > 0 || busy}
                className={`font-semibold ${
                  resendCooldown > 0
                    ? 'text-slate-400 dark:text-slate-500 cursor-not-allowed'
                    : 'text-brand-600 hover:text-brand-700 hover:underline dark:text-indigo-400 dark:hover:text-indigo-300'
                }`}
              >
                {resendCooldown > 0 ? `Resend code in ${resendCooldown}s` : 'Resend SMS code'}
              </button>
            </div>
          </div>
        ) : (
          /* ================= STEP 1: LOGIN / REGISTER SCREEN ================= */
          <>
            {mode === 'register' && (
              <div>
                <label className="label" htmlFor="name">
                  Full name
                </label>
                <input
                  id="name"
                  className="input"
                  value={fullName}
                  placeholder="e.g. Shubh Thakkar"
                  onChange={(e) => setFullName(e.target.value)}
                  autoComplete="name"
                />
              </div>
            )}

            <div>
              <label className="label" htmlFor="email">
                Email
              </label>
              <input
                id="email"
                type="email"
                required
                className="input"
                value={email}
                placeholder="name@example.com"
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
              />
            </div>

            <div>
              <label className="label" htmlFor="password">
                Password
              </label>
              <input
                id="password"
                type="password"
                required
                className="input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                minLength={mode === 'register' ? 8 : undefined}
              />
              {passwordHint && (
                <p className="mt-1 text-xs text-amber-700">
                  Use at least 8 characters with letters and digits.
                </p>
              )}
            </div>

            {/* Phone number for live Twilio SMS */}
            <div>
              <label className="label" htmlFor="phone">
                Phone number for Twilio 2-Step SMS
              </label>
              <input
                id="phone"
                type="tel"
                required
                placeholder="+91 98765 43210"
                className="input font-mono"
                value={phoneNumber}
                onChange={(e) => setPhoneNumber(e.target.value)}
                autoComplete="tel"
              />
              <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                Enter your mobile number with country code (e.g. +91 or +1). Twilio will send the verification code directly to this phone.
              </p>
            </div>

            <button type="submit" className="btn-primary w-full py-2.5 font-semibold mt-2" disabled={busy}>
              {busy && <Spinner />}{' '}
              {mode === 'login' ? 'Send Twilio SMS & Verify' : 'Create account'}
            </button>

            <p className="text-center text-sm text-slate-600 dark:text-slate-400 pt-1">
              {mode === 'login' ? 'New here?' : 'Already have an account?'}{' '}
              <button
                type="button"
                className="font-medium text-brand-600 hover:underline dark:text-indigo-400 dark:hover:text-indigo-300"
                onClick={() => {
                  setMode(mode === 'login' ? 'register' : 'login');
                  setError(null);
                  setResendSuccess(null);
                }}
              >
                {mode === 'login' ? 'Create an account' : 'Sign in'}
              </button>
            </p>

            {mode === 'register' && (
              <p className="text-center text-xs text-slate-500 dark:text-slate-400">
                The first account created becomes the administrator.
              </p>
            )}
          </>
        )}
      </form>

      <p className="mt-4 text-center text-sm text-slate-500 dark:text-slate-400">
        First time? Read the{' '}
        <Link href="/manual" className="font-medium text-brand-600 hover:underline dark:text-indigo-400 dark:hover:text-indigo-300">
          User Manual
        </Link>{' '}
        before scanning.
      </p>
    </div>
  );
}
