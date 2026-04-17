import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/app/**/*.{ts,tsx}",
    "./src/components/**/*.{ts,tsx}",
    "./src/lib/**/*.{ts,tsx}"
  ],
  theme: {
    extend: {
      colors: {
        ink: {
          900: "#060913",
          800: "#0B1020",
          700: "#111A30",
          600: "#1A2340",
          500: "#253055"
        },
        sonar: {
          cyan: "#22D3EE",
          green: "#34D399",
          red: "#F87171",
          amber: "#FBBF24",
          violet: "#A78BFA"
        }
      },
      fontFamily: {
        mono: ["JetBrains Mono", "IBM Plex Mono", "ui-monospace", "monospace"],
        sans: ["Inter", "ui-sans-serif", "system-ui"]
      },
      boxShadow: {
        glow: "0 0 24px -2px rgba(34, 211, 238, 0.35)"
      }
    }
  },
  plugins: []
};

export default config;
