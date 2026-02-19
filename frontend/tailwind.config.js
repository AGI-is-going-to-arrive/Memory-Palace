import defaultTheme from 'tailwindcss/defaultTheme';

/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        display: ['"Playfair Display"', 'Georgia', ...defaultTheme.fontFamily.serif],
        sans: ['Inter', ...defaultTheme.fontFamily.sans],
      },
      backgroundImage: {
        'palace-vignette':
          'radial-gradient(circle at 10% 0%, rgba(179, 133, 79, 0.26), transparent 42%), radial-gradient(circle at 92% 6%, rgba(214, 193, 163, 0.35), transparent 44%)',
      },
      boxShadow: {
        candle: '0 8px 24px rgba(108, 81, 53, 0.18)',
      },
    },
  },
  plugins: [],
}
