/**
 * Design tokens. v1: spec §8 + "darkfield". v2 (2026-10-30, owner request): an editorial look adapted from an
 * MIT-licensed landing template — warm bone paper, near-black instrument panels, one lime accent, Host Grotesk /
 * Instrument Serif italic / JetBrains Mono. Token names are kept so every screen re-skins from here.
 * Stain colours (dna, tubulin, actin) are for their channel only; verdict colours for the verdict only;
 * lime is UI accent only (buttons, live dot, emphasis) and never marks data.
 */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    colors: {
      transparent: "transparent",
      current: "currentColor",
      white: "#FFFFFF",
      paper: "#EBE6DC", // page background (bone)
      sheet: "#F7F4EE", // raised surface on paper
      ink: "#0D0C0B",
      graphite: "#73706B", // secondary text on paper
      darkfield: "#000000",
      night: "#0B0A09", // instrument body
      "night-2": "#151311", // raised instrument surface
      "night-3": "#1F1C19", // controls on the instrument
      mist: "#8F897F", // secondary text on night
      haze: "#EFE9DF", // primary text on night
      lime: "#D7FF3F", // UI accent only
      dna: "#5B8CFF",
      tubulin: "#3FCF7F",
      actin: "#FF5468",
      "verdict-yes": "#1E6B47",
      "verdict-no": "#A3302A",
      "verdict-yes-on-night": "#5FBF8F",
      "verdict-no-on-night": "#E07068",
    },
    extend: {
      fontFamily: {
        serif: ['"Host Grotesk"', "system-ui", "sans-serif"], // headings: display grotesk (was Source Serif)
        accent: ['"Instrument Serif"', "Georgia", "serif"], // italic emphasis only
        sans: ['"Host Grotesk"', "system-ui", "sans-serif"],
        display: ['"Host Grotesk"', "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "monospace"],
      },
      borderRadius: { control: "10px", panel: "16px", frame: "30px" },
      borderColor: { rule: "rgb(13 12 11 / 0.13)", line: "rgb(239 233 223 / 0.12)" },
      letterSpacing: { display: "-0.055em" },
    },
  },
  plugins: [],
};
