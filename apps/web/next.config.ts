import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Without this, Turbopack walks up looking for a lockfile and can settle on
  // one outside the repository, which changes what it resolves.
  turbopack: { root: __dirname },

  // A self-contained server bundle, so the Docker image does not need the
  // whole node_modules tree.
  output: "standalone",
};

export default nextConfig;
