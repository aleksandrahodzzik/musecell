/** Spec §8 tokens. Stain colours (dna, tubulin, actin) are for their channel only; verdict colours for the verdict only. */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
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
      dna: "#5B8CFF",
      tubulin: "#3FCF7F",
      actin: "#FF5468",
      "verdict-yes": "#1E6B47",
      "verdict-no": "#A3302A",
    },
    extend: {
      fontFamily: {
        serif: ['"Source Serif 4"', "Georgia", "serif"],
        sans: ['"IBM Plex Sans"', "system-ui", "sans-serif"],
      },
      borderRadius: { control: "6px", panel: "10px" },
      borderColor: { rule: "rgb(23 32 42 / 0.12)" },
    },
  },
  plugins: [],
};
