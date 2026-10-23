import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // "/" opens Today's Work. Older invoice links (/?invoice=<id>, also stored in API data) open the invoice on the
  // Invoices page; query strings are passed through to the destination.
  async redirects() {
    return [
      { source: "/", has: [{ type: "query", key: "invoice" }], destination: "/invoices", permanent: false },
      { source: "/", destination: "/today", permanent: false },
    ];
  },
};

export default nextConfig;
