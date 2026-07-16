import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emit a self-contained .next/standalone build for a minimal Docker image.
  output: "standalone",
  experimental: {
    // Dev-only: don't retain cloned fetch responses across HMR refreshes.
    // A minor WeakRef source feeding the Next 16.2.10 dev-server heap growth;
    // harmless to disable since RSC fetches here are client-side, not server-side.
    // NOTE: this does NOT fix the main leak (see the heap cap on `make web`);
    // measured only ~13% fewer retained WeakRefs per request.
    serverComponentsHmrCache: false,
  },
};

export default nextConfig;
