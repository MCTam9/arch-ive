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
    <html lang="en" className={fontVariables}>
      <body>
        <a href="#main" className="skip-link font-display">
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
