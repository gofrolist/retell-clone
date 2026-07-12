import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emit a self-contained .next/standalone build for a minimal Docker image.
  output: "standalone",
};

export default nextConfig;
