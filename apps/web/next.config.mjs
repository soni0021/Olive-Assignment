/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  experimental: {
    serverComponentsExternalPackages: ["openai", "@anthropic-ai/sdk"],
  },
};

export default nextConfig;
