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
    <main
      style={{
        minHeight: "100dvh",
        display: "grid",
        placeItems: "center",
        background: "var(--n900)",
      }}
    >
      <div
        className="card shadow-hard"
        style={{
          position: "relative",
          width: "min(420px, 90vw)",
          padding: "var(--s-8)",
          background: "var(--n100)",
        }}
      >
        <h1
          className="font-display"
          style={{
            fontSize: "var(--fs-h1)",
            // the strapline under this used to carry the gap to the button
            margin: "0 0 var(--s-6)",
            color: "var(--n900)",
          }}
        >
          arch-ive
        </h1>

        {error && (
          <p
            className="font-mono"
            style={{
              background: "var(--n200)",
              border: "var(--border-width) solid var(--n900)",
              padding: "var(--s-2)",
              marginBottom: "var(--s-4)",
              fontSize: "var(--fs-sm)",
            }}
          >
            Sign-in failed: no active allowlist entry for that account.
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
              border: "var(--border-width-strong) solid var(--n900)",
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
                border: "var(--border-width) solid var(--n900)",
                background: "var(--surface)",
              }}
            />
            <button
              type="submit"
              className="font-display transition-fast"
              style={{
                padding: "var(--s-2) var(--s-3)",
                background: "var(--n900)",
                color: "var(--n100)",
                border: "var(--border-width) solid var(--n900)",
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
