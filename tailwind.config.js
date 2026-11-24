/** Tailwind v3 config — compiled by the standalone CLI (no Node.js needed).
 *  Build:  bash scripts/bundle_static.sh --css-only
 */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./accounts/**/*.py",
    "./dashboard/**/*.py",
    "./exams/**/*.py",
    "./static/js/**/*.js",
  ],
  theme: {
    extend: {
      fontFamily: {
        // System fonts only: nothing to download, works offline everywhere.
        sans: ["ui-sans-serif", "system-ui", "Segoe UI", "Roboto", "Helvetica Neue", "Arial", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Consolas", "Liberation Mono", "monospace"],
      },
    },
  },
  plugins: [require("@tailwindcss/forms")],
};
