/** @type {import('next').NextConfig} */
const nextConfig = {
  // API backend URL (env var sau default local)
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000',
  },
  // Permite imagini de la orice domeniu
  images: { unoptimized: true },
  // Skip TypeScript type checking
  typescript: {
    ignoreBuildErrors: true,
  },
  // Disable ESLint
  eslint: {
    ignoreDuringBuilds: true,
  },
};

module.exports = nextConfig;
