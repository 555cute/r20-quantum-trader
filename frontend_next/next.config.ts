import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  output: 'export',
  distDir: 'out',
  trailingSlash: true,
  images: {
    unoptimized: true,
  },
  typescript: {
    ignoreBuildErrors: true,
  },
}

export default nextConfig
