import type { Config } from "tailwindcss";

// Light "credit memo" theme. The legacy dark-theme class names (slate, ink, emerald, rose,
// amber, sky) are remapped here so every screen moves to the new palette at once:
// slate-100…300 = ink text, 400…500 = muted text, 600…900 = rules and quiet surfaces.
const ink = "#1C232B";
const muted = "#5E6A72";
const quiet = "#66727A";
const rule = "#D5DAD9";
const added = "#B4472E"; // debt added / pressure up (brick)
const reduced = "#2E6B57"; // debt reduced / pressure down (deep green)
const watch = "#8A6416"; // worth watching (ochre, text-safe)
const info = "#2B4A66"; // links, live, focus

const ramp = (text: string, mid: string) => ({
  50: text, 100: text, 200: text, 300: text, 400: text, 500: mid, 600: mid, 700: mid, 800: mid, 900: mid,
});

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: "#F6F7F5",
        memo: { ink, muted, rule, added, reduced, watch, info, highlight: "#E6EF6E" },
        ink: { DEFAULT: "#F6F7F5", soft: "#E9ECE8" }, // legacy name: page surface
        accent: { DEFAULT: info },
        white: ink, // legacy text-white → ink
        slate: {
          50: ink, 100: ink, 200: ink, 300: "#2E3740", 400: muted, 500: quiet,
          600: "#8A949A", 700: rule, 800: "#E2E6E4", 900: "#ECEEEB", 950: "#F2F4F1",
        },
        emerald: ramp(reduced, reduced),
        rose: ramp(added, added),
        amber: ramp(watch, "#A87C1F"),
        sky: ramp(info, info),
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        serif: ["var(--font-serif)", "Georgia", "serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      fontSize: {
        xs: ["13px", "18px"],
        sm: ["14px", "21px"],
        base: ["15px", "24px"],
        lg: ["17px", "26px"],
        xl: ["22px", "30px"],
        "2xl": ["30px", "36px"],
        "3xl": ["40px", "46px"],
      },
    },
  },
  plugins: [],
};

export default config;
