import { NextResponse } from 'next/server';
import crypto from 'crypto';
import { otpStore } from '@/lib/otpStore';

export async function POST(req) {
  try {
    const { verification_id, code } = await req.json();

    if (!verification_id || !code) {
      return NextResponse.json({ error: 'Verification ID and code are required.' }, { status: 400 });
    }

    const entry = otpStore.get(verification_id);
    if (!entry) {
      return NextResponse.json({ error: 'Verification session expired. Please request a new code.' }, { status: 400 });
    }

    if (Date.now() > entry.expiresAt) {
      otpStore.delete(verification_id);
      return NextResponse.json({ error: 'Verification code expired. Please request a new code.' }, { status: 400 });
    }

    entry.attempts += 1;
    if (entry.attempts > 5) {
      otpStore.delete(verification_id);
      return NextResponse.json({ error: 'Too many invalid attempts. Please sign in again.' }, { status: 429 });
    }

    if (String(code).trim() !== String(entry.code).trim()) {
      return NextResponse.json({ error: 'Invalid verification code. Please check your SMS and try again.' }, { status: 400 });
    }

    otpStore.delete(verification_id);

    // Verified!
    const user = {
      id: crypto.randomUUID(),
      email: entry.email || 'user@example.com',
      full_name: 'Authorized User',
      phone_number: entry.toPhone,
      role: 'admin',
      created_at: new Date().toISOString(),
      last_login_at: new Date().toISOString(),
    };

    return NextResponse.json({
      access_token: 'valid-frs-token-' + crypto.randomUUID(),
      token_type: 'bearer',
      expires_at: new Date(Date.now() + 8 * 3600e3).toISOString(),
      user,
    });
  } catch (err) {
    console.error('verify-otp API error:', err);
    return NextResponse.json({ error: err.message || 'Internal server error verifying OTP' }, { status: 500 });
  }
}
