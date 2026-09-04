// Font loading for the OFL families, kept apart from tokens.css so the token
// file stays pure CSS. The display face is Argent Pixel CF, which is a
// commercial licence and so is self-hosted via @font-face in tokens.css from
// files that are never committed — Jacquard 12 below is what a clone without
// them falls back to, not decoration. Departure Mono is likewise not on
// Google Fonts; tokens.css names it first in --font-mono as an honest local
// fallback, with Silkscreen/VT323 actually loaded behind it.
import { Geist, Jacquard_12, Pixelify_Sans, Silkscreen, VT323 } from "next/font/google";

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

export const silkscreen = Silkscreen({
  subsets: ["latin"],
  weight: ["400", "700"],
  variable: "--font-silkscreen",
  display: "swap",
});

export const vt323 = VT323({
  subsets: ["latin"],
  weight: "400",
  variable: "--font-vt323",
  display: "swap",
});

export const geist = Geist({
  subsets: ["latin"],
  variable: "--font-geist",
  display: "swap",
});

export const fontVariables = [
  jacquard12.variable,
  pixelifySans.variable,
  silkscreen.variable,
  vt323.variable,
  geist.variable,
].join(" ");
