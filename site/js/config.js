// API base for the Flask backend. Override per-environment by editing this
// file at deploy time (GitHub Pages serves static files as-is, no env vars).
window.RPSAS_CONFIG = {
  apiBase: (location.hostname === "localhost" || location.hostname === "127.0.0.1")
    ? "http://localhost:5050"
    : "https://api.sixthvital.example.com",
};
