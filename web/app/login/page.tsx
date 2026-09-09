import { signIn, auth } from "@/auth";
import { redirect } from "next/navigation";
import { Button } from "@/components/ui";
import { safePath } from "@/lib/links";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; next?: string }>;
}) {
  const { error, next } = await searchParams;

  // Validated here as well as where it is set: `next` arrives in a URL anyone
  // can edit, and an unchecked one is an open redirect on the one page whose
  // whole job is to be trusted.
  const target = safePath(next) ?? "/";

  const session = await auth();
  if (session?.accountId) redirect(target);

  const devLoginEnabled =
    process.env.NODE_ENV !== "production" && process.env.AUTH_DEV_LOGIN === "true";

  return (
    // id="main" so the skip link in app/layout.tsx has a target here too --
    // this route is outside (protected), which is where that id otherwise lives.
    <main
      id="main"
      style={{
        minHeight: "100dvh",
        display: "grid",
        placeItems: "center",
        background: "var(--bg)",
      }}
    >
      <div
        className="card shadow-hard"
        style={{
          position: "relative",
          width: "min(420px, 90vw)",
          padding: "var(--s-8)",
          background: "var(--surface)",
        }}
      >
        <h1
          className="font-display"
          style={{
            fontSize: "var(--fs-h1)",
            // The strapline below carries the gap to the button, so the
            // wordmark only needs to clear its own descender.
            margin: "0 0 var(--s-2)",
            color: "var(--text)",
          }}
        >
          arch-ive
        </h1>

        {/* Body font, not the bitmap face: this is a sentence, and Rule 1
            keeps the display face on chrome. It is also the only thing on the
            page that says what you are signing in to. */}
        <p
          className="font-body text-muted"
          style={{ fontSize: "var(--fs-sm)", margin: "0 0 var(--s-6)" }}
        >
          Architecture knowledge base
        </p>

        {error && (
          <p
            className="font-mono"
            style={{
              // Semantic tokens, not raw ramp values. This block used to set
              // background: var(--n200) with no colour of its own, so it
              // inherited --text -- near-white in dark mode, on a near-white
              // fixed background. The one message a user sees when they
              // cannot get in was invisible to half of them.
              background: "var(--surface-sunken)",
              color: "var(--text)",
              border: "var(--border-width) solid var(--border-strong)",
              padding: "var(--s-2)",
              marginBottom: "var(--s-4)",
              fontSize: "var(--fs-sm)",
            }}
          >
            {/* A lookup that found nothing and a lookup that could not RUN mean
                completely different things, and this rendered both as the
                first. On 2026-09-09 the sign-in DSN in the deployment
                environment was a rotated-away password, so `arch_auth` could
                not connect at all — and this page told the owner their own
                account was not on the allowlist, while the row sat there,
                active. Two accounts were re-added chasing that message.

                So the accusatory message is now the SPECIAL case, not the
                default: Auth.js sends `AccessDenied` only when the signIn
                callback deliberately returned false, which is the one state we
                actually know means "not allowed". Everything else — a thrown
                lookup, a misconfiguration, an error code added by a future
                Auth.js — falls through to a message that blames this end,
                because that is where the fault almost certainly is. */}
            {error === "AccessDenied" ? (
              <>
                That Google account is not on the allowlist. Check which account
                you signed in with — the allowlist matches on email address.
              </>
            ) : (
              <>
                Could not check your access just now. That is a fault at this
                end, not with your account — try again in a moment. If it keeps
                happening, the sign-in database is unreachable.
              </>
            )}
          </p>
        )}

        <form
          action={async () => {
            "use server";
            await signIn("google", { redirectTo: target });
          }}
        >
          <Button variant="primary" style={{ width: "100%", padding: "var(--s-3)" }}>
            Sign in with Google
          </Button>
        </form>

        {devLoginEnabled && (
          <form
            action={async (formData: FormData) => {
              "use server";
              const email = String(formData.get("email") ?? "");
              await signIn("dev", { email, redirectTo: target });
            }}
            style={{ marginTop: "var(--s-4)", display: "flex", gap: "var(--s-2)" }}
          >
            <input
              name="email"
              type="email"
              placeholder="dev@local"
              required
              className="field font-mono"
              style={{ flex: 1 }}
            />
            <Button variant="secondary">Dev sign-in</Button>
          </form>
        )}
      </div>
    </main>
  );
}
