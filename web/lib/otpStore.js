// Shared OTP verification store for Next.js API routes
if (!globalThis._otpStore) {
  globalThis._otpStore = new Map();
}

export const otpStore = globalThis._otpStore;

export function maskPhone(p) {
  if (!p) return '+91 ••••• ••076';
  const clean = String(p).trim();
  if (clean.length <= 4) return clean;
  const last4 = clean.slice(-4);
  const prefix = clean.slice(0, 3);
  return `${prefix} ••••• ••${last4.slice(-2)}`;
}
