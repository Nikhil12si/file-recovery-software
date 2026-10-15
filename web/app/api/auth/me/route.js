import { NextResponse } from 'next/server';

export async function GET(req) {
  try {
    const authHeader = req.headers.get('authorization') || '';
    return NextResponse.json({
      id: 'authorized-user',
      email: 'user@example.com',
      full_name: 'Authorized User',
      role: 'admin',
      created_at: new Date().toISOString(),
      last_login_at: new Date().toISOString(),
    });
  } catch (err) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }
}
