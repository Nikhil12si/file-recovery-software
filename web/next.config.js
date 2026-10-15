/** @type {import('next').NextConfig} */
// Two build modes:
//  * Local (default): `npm run build && npm start`. Browser calls go to same-origin /api/*, proxied below to
//    the FastAPI service, so no CORS configuration or hard-coded backend URL is needed.
//  * Hosted (NEXT_EXPORT=true, used by scripts/deploy-firebase.ps1): a static site for Firebase Hosting in
//    web/out. It has no server, so there are no rewrites; the browser calls the visitor's local Recovery
//    Agent directly at NEXT_PUBLIC_API_BASE (http://127.0.0.1:8000).
const BACKEND_URL = process.env.BACKEND_URL || 'http://127.0.0.1:8000';
const STATIC_EXPORT = process.env.NEXT_EXPORT === 'true';

module.exports = {
  reactStrictMode: true,
  poweredByHeader: false,
  ...(STATIC_EXPORT
    ? { output: 'export', images: { unoptimized: true } }
    : {
      async rewrites() {
        if (process.env.NEXT_PUBLIC_USE_MOCKS !== 'false') return [];
        return [{ source: '/api/:path*', destination: `${BACKEND_URL}/api/:path*` }];
      },
    }),
};
