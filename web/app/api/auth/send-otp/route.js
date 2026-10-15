import { NextResponse } from 'next/server';
import crypto from 'crypto';
import { otpStore, maskPhone } from '@/lib/otpStore';

export async function POST(req) {
  try {
    const body = await req.json();
    let toPhone = String(body.to_phone || body.phone_number || '').trim().replace(/[\s\-()]/g, '');
    
    if (!toPhone) {
      return NextResponse.json({ error: 'Please enter a valid phone number.' }, { status: 400 });
    }

    if (!toPhone.startsWith('+')) {
      if (toPhone.length === 10) {
        toPhone = '+91' + toPhone;
      } else {
        toPhone = '+' + toPhone;
      }
    }

    // Generate real 6-digit random code
    const code = String(crypto.randomInt(100000, 999999));
    const verificationId = crypto.randomUUID();

    const accountSid = process.env.TWILIO_ACCOUNT_SID;
    const authToken = process.env.TWILIO_AUTH_TOKEN;
    const fromNumber = process.env.TWILIO_PHONE_NUMBER;

    if (!accountSid || !authToken || !fromNumber) {
      return NextResponse.json({
        error: 'Twilio credentials not configured. Please set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_PHONE_NUMBER in web/.env.local.',
      }, { status: 500 });
    }

    const authHeader = 'Basic ' + Buffer.from(`${accountSid}:${authToken}`).toString('base64');
    const params = new URLSearchParams({
      To: toPhone,
      From: fromNumber,
      Body: `Your HDD File Recovery verification code is: ${code}. Valid for 5 minutes.`,
    });

    const twilioRes = await fetch(`https://api.twilio.com/2010-04-01/Accounts/${accountSid}/Messages.json`, {
      method: 'POST',
      headers: {
        'Authorization': authHeader,
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: params.toString(),
    });

    const twilioData = await twilioRes.json();

    if (!twilioRes.ok) {
      console.error('Twilio SMS Error:', twilioData);
      let errMsg = twilioData.message || 'Twilio failed to send SMS message.';
      if (twilioData.code === 21608) {
        errMsg = `Twilio Trial Account Notice: The number ${toPhone} must be verified in your Twilio Console (twilio.com/user/account/phone-numbers/verified) to receive trial SMS.`;
      }
      return NextResponse.json({ error: errMsg, code: twilioData.code }, { status: 400 });
    }

    // Store verified code in memory
    otpStore.set(verificationId, {
      code,
      toPhone,
      email: body.email || 'user@example.com',
      expiresAt: Date.now() + 5 * 60 * 1000,
      attempts: 0,
    });

    console.log(`[Twilio SMS Sent] Code dispatched to ${toPhone}, SID: ${twilioData.sid}`);

    return NextResponse.json({
      success: true,
      two_factor_required: true,
      verification_id: verificationId,
      phone_masked: maskPhone(toPhone),
      message: `Verification code was sent via Twilio SMS to ${maskPhone(toPhone)}.`,
    });
  } catch (err) {
    console.error('send-otp API error:', err);
    return NextResponse.json({ error: err.message || 'Internal server error sending SMS' }, { status: 500 });
  }
}
