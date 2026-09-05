import { signIn, auth } from "@/auth";
import { redirect } from "next/navigation";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const session = await auth();
  if (session?.accountId) redirect("/");

  const { error } = await searchParams;
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
            // the strapline under this used to carry the gap to the button
            margin: "0 0 var(--s-6)",
            color: "var(--text)",
          }}
        >
          arch-ive
        </h1>

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
            That Google account is not on the allowlist. Check which account you
            signed in with — the allowlist matches on email address.
          </p>
        )}

        <form
          action={async () => {
            "use server";
            await signIn("google", { redirectTo: "/" });
          }}
        >
          <button
            type="submit"
            className="font-display transition-fast"
            style={{
              width: "100%",
              padding: "var(--s-3)",
              background: "var(--accent)",
              color: "var(--accent-text)",
              border: "var(--border-width-strong) solid var(--border-strong)",
              fontSize: "var(--fs-label)",
              cursor: "pointer",
            }}
          >
            Sign in with Google
          </button>
        </form>

        {devLoginEnabled && (
          <form
            action={async (formData: FormData) => {
              "use server";
              const email = String(formData.get("email") ?? "");
              await signIn("dev", { email, redirectTo: "/" });
            }}
            style={{ marginTop: "var(--s-4)", display: "flex", gap: "var(--s-2)" }}
          >
            <input
              name="email"
              type="email"
              placeholder="dev@local"
              required
              className="font-mono"
              style={{
                flex: 1,
                padding: "var(--s-2)",
                border: "var(--border-width) solid var(--border-strong)",
                background: "var(--surface)",
              }}
            />
            <button
              type="submit"
              className="font-display transition-fast"
              style={{
                padding: "var(--s-2) var(--s-3)",
                background: "var(--surface-sunken)",
                color: "var(--text)",
                border: "var(--border-width) solid var(--border-strong)",
                fontSize: "var(--fs-label)",
                cursor: "pointer",
              }}
            >
              Dev sign-in
            </button>
          </form>
        )}
      </div>
    </main>
  );
}
