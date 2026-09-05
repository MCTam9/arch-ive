// Auth gate for protected routes. Deliberately NOT done in proxy.ts: Next 16
// renamed middleware to Proxy and its own docs say it "should not be used as
// a full session management or authorization solution" — and in this app it
// would also be the wrong runtime, since the allowlist check in auth.ts's
// callbacks goes through `pg`, which needs the Node.js runtime that Proxy
// doesn't guarantee. Instead every protected route sits under
// app/(protected)/layout.tsx, which calls requireSession() once per request.
import { cache } from "react";
import { redirect } from "next/navigation";
import { auth } from "@/auth";

// Wrapped in React's request-scoped cache() because the layout calls this and
// then so does every page under it — two JWT decodes per request for one
// answer that cannot change mid-render. cache() dedupes within a single
// request and shares nothing across requests, so a session is never reused by
// a different visitor.
export const requireSession = cache(async () => {
  const session = await auth();
  if (!session?.accountId) {
    redirect("/login");
  }
  return session as NonNullable<typeof session> & { accountId: string };
});
