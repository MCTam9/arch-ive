// The DSN both halves of the suite read.
//
// Same variable name as tests/conftest.py so the Python and TypeScript suites
// cannot end up pointed at different databases — CI sets it once, at job
// level, for both.
export const TEST_DSN =
  process.env.TEST_DATABASE_URL ??
  "postgresql://arch_app:dev@localhost:55432/arch_test";

// db/test_account.sql inserts exactly this id into allowed_account. RLS is
// FORCED and its policies call has_access(), so a query scoped to any other
// account id returns zero rows silently rather than erroring.
export const TEST_ACCOUNT_ID = "00000000-0000-0000-0000-0000000000aa";
