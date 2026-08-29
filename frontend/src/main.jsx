import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
// Inter, served from this origin — no request to Google Fonts on page load.
import "@fontsource/inter/300.css";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "./index.css"; // Basic global styles

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
