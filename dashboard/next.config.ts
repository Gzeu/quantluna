import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  // API backend URL (env var sau default local)
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000',
  },
  // Permite imagini de la orice domeniu
  images: { unoptimized: true },
  // Output static pentru Docker nginx
  output: 'standalone',
};

export default nextConfig;
