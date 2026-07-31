/**
 * tailwind.config.js — EIR Desktop v1 (build-only)
 *
 * Tokens fusionados de los 2 mockups "Organic Professional" de Stitch
 * (ver carpeta contexto_stitch, subcarpetas organic_refactor, archivo code.html).
 * Este config NO se ejecuta en runtime: solo alimenta el build de
 * `npx tailwindcss` que genera static_desktop/vendor/tailwind_desktop.css
 * (vendorizado, sin CDN, para que la app funcione offline).
 */
const path = require("path");

module.exports = {
  darkMode: "class",
  content: [
    path.join(__dirname, "..", "templates").split(path.sep).join("/") + "/**/*.html",
  ],
  theme: {
    extend: {
      colors: {
        "surface-container-highest": "#e3e3de",
        "on-surface-variant": "#42493e",
        "tertiary": "#553112",
        "surface-variant": "#e3e3de",
        "inverse-surface": "#2f312e",
        "on-background": "#1a1c19",
        "inverse-primary": "#a1d494",
        "surface": "#fafaf4",
        "on-tertiary-fixed": "#301400",
        "on-secondary": "#ffffff",
        "on-secondary-fixed-variant": "#48473c",
        "on-error": "#ffffff",
        "tertiary-container": "#704727",
        "error": "#ba1a1a",
        "secondary": "#605f53",
        "surface-container": "#eeeee9",
        "on-tertiary-container": "#f0b78f",
        "outline-variant": "#c2c9bb",
        "inverse-on-surface": "#f1f1ec",
        "on-tertiary-fixed-variant": "#653d1e",
        "secondary-fixed-dim": "#cac7b8",
        "secondary-container": "#e6e3d4",
        "surface-tint": "#3b6934",
        "on-primary-fixed-variant": "#23501e",
        "on-primary-container": "#9dd090",
        "on-primary": "#ffffff",
        "surface-bright": "#fafaf4",
        "tertiary-fixed": "#ffdcc5",
        "outline": "#72796e",
        "on-secondary-container": "#676559",
        "error-container": "#ffdad6",
        "primary-container": "#2d5a27",
        "secondary-fixed": "#e6e3d4",
        "on-surface": "#1a1c19",
        "surface-container-high": "#e8e8e3",
        "surface-container-lowest": "#ffffff",
        "primary-fixed-dim": "#a1d494",
        "on-tertiary": "#ffffff",
        "on-error-container": "#93000a",
        "on-secondary-fixed": "#1d1c13",
        "surface-container-low": "#f4f4ee",
        "background": "#fafaf4",
        "on-primary-fixed": "#002201",
        "primary": "#154212",
        "tertiary-fixed-dim": "#f4bb92",
        "surface-dim": "#dadad5",
        "primary-fixed": "#bcf0ae",
      },
      borderRadius: {
        DEFAULT: "0.75rem",
        lg: "0.75rem",
        xl: "1rem",
        full: "9999px",
      },
      spacing: {
        unit: "8px",
        margin: "32px",
        "sidebar-width": "280px",
        gutter: "24px",
        "input-height": "48px",
      },
      fontFamily: {
        "body-md": ["Nunito Sans", "sans-serif"],
        "headline-lg": ["Nunito Sans", "sans-serif"],
        "label-caps": ["Nunito Sans", "sans-serif"],
        "tech-log": ["Nunito Sans", "sans-serif"],
        "headline-xl": ["Nunito Sans", "sans-serif"],
        "label-sm": ["Nunito Sans", "sans-serif"],
        "headline-sm": ["Nunito Sans", "sans-serif"],
        "headline-md": ["Nunito Sans", "sans-serif"],
        "label-md": ["Nunito Sans", "sans-serif"],
        "body-lg": ["Nunito Sans", "sans-serif"],
      },
      fontSize: {
        "label-caps": ["11px", { lineHeight: "16px", letterSpacing: "0.08em", fontWeight: "800" }],
        "body-sm": ["14px", { lineHeight: "20px", fontWeight: "400" }],
        "body-md": ["16px", { lineHeight: "24px", fontWeight: "400" }],
        "headline-lg": ["32px", { lineHeight: "40px", letterSpacing: "-0.01em", fontWeight: "700" }],
        "tech-log": ["13px", { lineHeight: "18px", fontWeight: "600" }],
        "headline-xl": ["40px", { lineHeight: "48px", letterSpacing: "-0.02em", fontWeight: "800" }],
        "label-sm": ["12px", { lineHeight: "16px", fontWeight: "700" }],
        "headline-sm": ["24px", { lineHeight: "32px", fontWeight: "700" }],
        "headline-md": ["32px", { lineHeight: "40px", fontWeight: "700" }],
        "label-md": ["14px", { lineHeight: "20px", letterSpacing: "0.01em", fontWeight: "600" }],
        "body-lg": ["18px", { lineHeight: "28px", fontWeight: "400" }],
      },
    },
  },
  plugins: [],
};
