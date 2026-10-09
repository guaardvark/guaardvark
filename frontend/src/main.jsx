import React from "react";
import ReactDOM from "react-dom/client";
import axios from "axios";
import App from "./App.jsx";
import { installBackendCredentials } from "./api/apiAuth";
import { installQueueMessages } from "./api/taskQueue";
import { reloadOnce } from "./utils/lazyWithReload";
// Every font is served from this origin — no request to Google Fonts on page
// load. Inter is the base face; the themes use Lato for body text and Raleway
// for headings, at the weights the old Google Fonts link asked for.
import "@fontsource/inter/300.css";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "@fontsource/lato/300.css";
import "@fontsource/lato/400.css";
import "@fontsource/lato/700.css";
import "@fontsource/raleway/200.css";
import "@fontsource/raleway/300.css";
import "@fontsource/raleway/400.css";
import "@fontsource/raleway/500.css";
import "@fontsource/raleway/600.css";
import "./index.css"; // Basic global styles

// Before the first render: axios refusals become Settings → Access advice,
// and, only when the build names a backend on another origin, requests to it
// carry this browser's sign-in cookie.
installBackendCredentials({ axios });
// A 503 for background work Redis did not take says so in the UI's words.
installQueueMessages({ axios });

// A built bundle whose chunks were replaced since this tab loaded: reload once
// instead of throwing; a repeat inside the guard window still throws.
window.addEventListener("vite:preloadError", (event) => {
  if (reloadOnce()) event.preventDefault();
});

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
