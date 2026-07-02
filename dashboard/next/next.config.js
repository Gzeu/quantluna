/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',           // static export -> /out -> FastAPI serve
  trailingSlash: true,
  images: { unoptimized: true },
  // API proxy in dev mode -> FastAPI :8000
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
