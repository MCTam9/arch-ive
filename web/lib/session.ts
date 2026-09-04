// Auth gate for protected routes. Deliberately NOT done in proxy.ts: Next 16
// renamed middleware to Proxy and its own docs say it "should not be used as
// a full session management or authorization solution" — and in this app it
// would also be the wrong runtime, since the allowlist check in auth.ts's
// callbacks goes through `pg`, which needs the Node.js runtime that Proxy
// doesn't guarantee. Instead every protected route sits under
// app/(protected)/layout.tsx, which calls requireSession() once per request.
import { redirect } from "next/navigation";
import { auth } from "@/auth";

export async function requireSession() {
  const session = await auth();
  if (!session?.accountId) {
    redirect("/login");
  }
  return session as typeof session & { accountId: string };
}
