/**
 * Tailwind CSS v4 configuration reference.
 *
 * NOTE: Tailwind v4 reads configuration from CSS (`@theme inline` in globals.css),
 * not from this file. This file is kept for tooling compatibility and documentation.
 *
 * Font family is declared in globals.css:
 *   @theme inline { --font-sans: 'Inter', system-ui, sans-serif; }
 *
 * See: https://tailwindcss.com/docs/v4-upgrade#configuration-reference
 */

import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: [
    "./pages/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./app/**/*.{ts,tsx}",
    "./src/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};

export default config;
