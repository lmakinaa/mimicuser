/** @type {import('next').NextConfig} */
const API = process.env.API_URL ?? "http://localhost:8000";

const nextConfig = {
  // Proxy the API through Next so the browser makes same-origin requests and
  // artifact <img> tags resolve without CORS or absolute URLs.
  async rewrites() {
    return [{ source: "/v1/:path*", destination: `${API}/v1/:path*` }];
  },
};

export default nextConfig;
