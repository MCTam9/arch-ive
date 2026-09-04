// Auth.js (NextAuth v5) + Google OAuth. A Google sign-in only succeeds if an
// `active` row exists in allowed_account for that email — checked in the
// signIn callback against the live table (see lib/db.ts for why that lookup
// needs its own elevated connection). No allowlist row, no session: full
// stop, regardless of whether Google itself accepted the login.
import NextAuth, { type DefaultSession } from "next-auth";
import type { JWT } from "next-auth/jwt";
import Credentials from "next-auth/providers/credentials";
import Google from "next-auth/providers/google";
import { findAllowedAccountByEmail, touchLastSeen } from "@/lib/db";

declare module "next-auth" {
  interface Session {
    accountId?: string;
    role?: string;
    user?: DefaultSession["user"];
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    accountId?: string;
    role?: string;
  }
}

// Dev-only escape hatch: this repo's live corpus has no real Google OAuth
// app registered against it, so there is no way to drive the Google consent
// screen from an automated/headless environment. When AUTH_DEV_LOGIN=true
// (never set in production; absent from .env.example on purpose) a
// Credentials provider is added that accepts a bare email and runs it
// through the EXACT SAME allowed_account gate as Google sign-in below. It
// does not bypass the allowlist — it only bypasses the OAuth handshake.
const devLoginEnabled =
  process.env.NODE_ENV !== "production" && process.env.AUTH_DEV_LOGIN === "true";

export const { handlers, auth, signIn, signOut } = NextAuth({
  trustHost: true,
  pages: {
    signIn: "/login",
    error: "/login",
  },
  session: { strategy: "jwt" },
  providers: [
    Google,
    ...(devLoginEnabled
      ? [
          Credentials({
            id: "dev",
            name: "Dev login (allowlisted email only)",
            credentials: {
              email: { label: "Email", type: "email" },
            },
            async authorize(credentials) {
              const email = String(credentials?.email ?? "").trim();
              if (!email) return null;
              return { id: email, email, name: email };
            },
          }),
        ]
      : []),
  ],
  callbacks: {
    async signIn({ user }) {
      if (!user.email) return false;
      const account = await findAllowedAccountByEmail(user.email);
      if (!account || account.status !== "active") return false;
      await touchLastSeen(account.id).catch(() => {
        // last_seen_at is a courtesy, not a gate — never block sign-in on it
      });
      return true;
    },
    async jwt({ token, user }: { token: JWT; user?: { email?: string | null } }) {
      if (user?.email) {
        const account = await findAllowedAccountByEmail(user.email);
        if (account) {
          token.accountId = account.id;
          token.role = account.role;
        }
      }
      return token;
    },
    async session({ session, token }) {
      session.accountId = token.accountId;
      session.role = token.role;
      return session;
    },
  },
});
