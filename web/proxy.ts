import { NextResponse, type NextRequest } from "next/server";
import { BROWSE_PARAMS, BROWSE_PATH } from "@/lib/links";

// One job: tell server components which URL they are rendering.
//
// This is not the thing lib/session.ts argues against. That comment rejects
// doing *authorization* here — Next's own docs say Proxy "should not be used
// as a full session management or authorization solution", and the allowlist
// check goes through `pg`, which needs a runtime Proxy does not guarantee.
// That reasoning still holds and the gate is still requireSession() inside
// app/(protected)/layout.tsx.
//
// What this does instead is the documented way to expose the request path to a
// server component, which otherwise cannot read it. requireSession() needs it
// for one reason: when it bounces a signed-out visitor to /login it has to be
// able to say where they were going, or a shared /browse?topic=… link survives
// the sign-in and arrives as the home page with the filters gone.
//
// It sets a header and gets out of the way. If it ever fails, the worst case
// is that `next` is absent and login falls back to home — the same behaviour
// as before it existed. It is never the thing keeping anyone out.

export function proxy(request: NextRequest) {
  const url = request.nextUrl;

  // Browse used to live at `/`, so every browse URL ever shared looks like
  // `/?topic=…&q=…`. Those links are the entire point of lib/links.ts and they
  // keep working. Done here rather than in the page because a redirect thrown
  // during rendering lands after the layout has already streamed, so the
  // response is a 200 carrying a client-side hop; here it is a real 307 before
  // anything renders, which is what a shared link deserves.
  if (url.pathname === "/" && BROWSE_PARAMS.some((p) => url.searchParams.has(p))) {
    const to = new URL(BROWSE_PATH, url);
    to.search = url.search;
    return NextResponse.redirect(to);
  }

  const headers = new Headers(request.headers);
  headers.set("x-pathname", url.pathname + url.search);
  return NextResponse.next({ request: { headers } });
}

export const config = {
  // Static assets and the auth callbacks have no use for the header, and the
  // callbacks in particular should carry exactly the headers they arrived with.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|api/auth).*)"],
};
