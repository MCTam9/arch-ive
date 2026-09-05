import type { Metadata } from "next";
import "./globals.css";
import { fontVariables } from "./fonts";

export const metadata: Metadata = {
  title: "arch-ive",
  description: "Architecture knowledge base — faceted browse over the corpus.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={fontVariables} suppressHydrationWarning>
      <head>
        {/* Applies the stored theme before first paint. Without this the page
            renders in the OS theme and then flips, which is worse than having
            no toggle at all. It must be inline and synchronous in <head> —
            anything deferred happens after the first frame. */}
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var t=localStorage.getItem('arch-ive-theme');if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t)}catch(e){}`,
          }}
        />
      </head>
      <body>
        <a href="#main" className="skip-link font-display">
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
