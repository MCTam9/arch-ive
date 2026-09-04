// Font loading, kept apart from tokens.css so the token file stays pure CSS
// (inspectable on its own, e.g. by a static reader). All four families are
// free/OFL and served from Google Fonts. Departure Mono is NOT on Google
// Fonts — tokens.css lists it first in --font-mono as an honest fallback
// name (a system that happens to have it locally will use it) with
// Silkscreen/VT323 actually loaded as the bitmap-mono fallback per spec.
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
