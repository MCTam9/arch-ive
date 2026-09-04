import { requireSession } from "@/lib/session";
import { Nav } from "@/components/nav";

export default async function ProtectedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await requireSession();
  return (
    <>
      <Nav email={session.user?.email} />
      <main id="main" style={{ minHeight: "100dvh", background: "var(--bg)" }}>
        {children}
      </main>
    </>
  );
}
