/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        az: {
          primary:    "#6B2D88",
          secondary:  "#3C1053",
          accent:     "#00A0DF",
          bg:         "#FFFFFF",
          surface:    "#F7F7F7",
          border:     "#E5E5E5",
          textPrimary:"#1A1A1A",
          textSecondary:"#6B6B6B",
          success:    "#00843D",
          warning:    "#F5A623",
          error:      "#D32F2F",
        },
      },
      fontFamily: {
        sans: ["Inter", "-apple-system", "BlinkMacSystemFont", "sans-serif"],
      },
      borderRadius: {
        card: "8px",
        btn:  "4px",
      },
      boxShadow: {
        az: "0 1px 3px rgba(0,0,0,0.08)",
      },
      spacing: {
        "az-1": "8px",
        "az-2": "16px",
        "az-3": "24px",
        "az-4": "32px",
        "az-6": "48px",
      },
    },
  },
  plugins: [],
}
