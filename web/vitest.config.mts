import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const webRoot = fileURLToPath(new URL("./", import.meta.url));

export default defineConfig({
  // Vite loads `.env` files from `envDir` and lifts them onto process.env
  // before any test runs. Its default is the project root, and web/.env
  // points DATABASE_URL at the DEV database holding the real corpus — so the
  // default would hand every test the one connection tests/setup.ts exists to
  // keep them away from. check_wat.py forbids a web/.env.test, so the fix
  // cannot be another env file: point envDir at a directory that holds none.
  envDir: fileURLToPath(new URL("./tests/env/", import.meta.url)),
  resolve: {
    // Mirrors tsconfig's `"@/*": ["./*"]`. The regex form (rather than a
    // "@/" string prefix) is what stops a scoped package like @scope/pkg
    // being rewritten into a path on disk.
    alias: [{ find: /^@\//, replacement: webRoot }],
  },
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
    setupFiles: ["./tests/setup.ts"],
    // Both halves of the suite share one arch_test database, and so does the
    // Python suite. Parallel files would interleave fixture writes under the
    // same document slugs.
    fileParallelism: false,
    // closePools() waits for checked-out clients rather than killing them, so
    // a leaked client shows up here as a timeout instead of a silent exit.
    teardownTimeout: 15_000,
    hookTimeout: 30_000,
  },
});
