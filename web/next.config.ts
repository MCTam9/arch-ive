import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Next writes its own CLAUDE.md and AGENTS.md into web/ on every dev run.
  // The repo already has a CLAUDE.md at the root defining how this project is
  // built; a second, framework-generated one two directories down is noise at
  // best and contradicts it at worst.
  agentRules: false,
};

export default nextConfig;
