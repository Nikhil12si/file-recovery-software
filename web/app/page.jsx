'use client';
import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

// Client-side redirect so the page also works in the static (Firebase Hosting) export.
export default function Home() {
  const router = useRouter();
  useEffect(() => {
    router.replace('/dashboard');
  }, [router]);
  return null;
}
