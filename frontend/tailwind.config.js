/**
 * Spec §8 tokens plus the instrument ("night") surfaces of the darkfield redesign (docs/DECISIONS.md, design pass).
 * Stain colours (dna, tubulin, actin) are for their channel only; verdict colours for the verdict only.
 */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    colors: {
      transparent: "transparent",
      current: "currentColor",
      white: "#FFFFFF",
      paper: "#E8ECEF",
      sheet: "#F6F8F9",
      ink: "#17202A",
      graphite: "#5B6672",
      darkfield: "#000000",
      night: "#07090C", // instrument body
      "night-2": "#0E1217", // raised instrument surface
      "night-3": "#161B22", // controls on the instrument
      mist: "#9AA5B1", // secondary text on night
      haze: "#D5DCE3", // primary text on night
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
        serif: ['"Source Serif 4"', "Georgia", "serif"],
        sans: ['"IBM Plex Sans"', "system-ui", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "monospace"],
      },
      borderRadius: { control: "6px", panel: "10px" },
      borderColor: { rule: "rgb(23 32 42 / 0.12)", line: "rgb(255 255 255 / 0.09)" },
    },
  },
  plugins: [],
};
