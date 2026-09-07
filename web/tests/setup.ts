// Point every test at a throwaway database. The TypeScript analogue of
// tests/conftest.py, and it exists for the reason recorded there: the dev
// database holds the real ingested corpus, fixtures create and delete rows
// under the same document slugs the corpus uses, and sharing one database
// silently deleted real rows -- twice, before the Python side grew this guard.
//
// Assignment, never ??=: web/.env is read by `next dev` in the same shell and
// a DATABASE_URL already on the environment is exactly the one to overwrite.
import { afterAll } from "vitest";
import { closePools } from "@/lib/db";
import { TEST_ACCOUNT_ID, TEST_DSN } from "./support";

// The name check is the load-bearing half. Overwriting DATABASE_URL only
// helps if what it is overwritten *with* is disposable, and "postgresql://…/
// arch" differs from "…/arch_test" by five characters in a variable nobody
// reads twice.
const database = new URL(TEST_DSN).pathname.replace(/^\//, "");
if (database !== "arch_test") {
  throw new Error(
    `Refusing to run: TEST_DATABASE_URL names database "${database}", not "arch_test". ` +
      `These tests create and drop rows under corpus document slugs; pointed at the dev ` +
      `database they delete real content.`,
  );
}

process.env.DATABASE_URL = TEST_DSN;
// The web app opens a second pool as arch_auth for the sign-in lookup. Tests
// do not exercise sign-in, but an unset AUTH_DB_URL turns any accidental call
// into a confusing requireEnv() throw rather than a connection to the throwaway.
process.env.AUTH_DB_URL = TEST_DSN;
process.env.ARCHIVE_ACCOUNT_ID = TEST_ACCOUNT_ID;

afterAll(closePools);
