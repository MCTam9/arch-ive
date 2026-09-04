// Font loading for the OFL families, kept apart from tokens.css so the token
// file stays pure CSS. The display face is Argent Pixel CF, which is a
// commercial licence and so is self-hosted via @font-face in tokens.css from
// files that are never committed -- Jacquard 12 below is what a clone without
// them falls back to, not decoration.
//
// JetBrains Mono carries both the data and the prose roles. Silkscreen, VT323
// and Geist were dropped rather than left loading unused.
import { Geist_Mono, JetBrains_Mono, Jacquard_12, Pixelify_Sans } from "next/font/google";

export const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  // 400 for prose, 500/700 for the data readouts that used to rely on a
  // different family to stand out.
  weight: ["400", "500", "700"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

export const jacquard12 = Jacquard_12({
  subsets: ["latin"],
  weight: "400",
  variable: "--font-jacquard-12",
  display: "swap",
});

export const pixelifySans = Pixelify_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-pixelify-sans",
  display: "swap",
});

// Kept only as the metric-compatible fallback behind JetBrains Mono.
export const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
  display: "swap",
});

export const fontVariables = [
  jetbrainsMono.variable,
  jacquard12.variable,
  pixelifySans.variable,
  geistMono.variable,
].join(" ");
