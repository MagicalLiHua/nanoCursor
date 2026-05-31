import React from "react";
import ReactDOM from "react-dom/client";
import "./styles/variables.css";
import "./styles/layout.css";
import "./styles/sidebar.css";
import "./styles/chat.css";
import "./styles/right-panel.css";
import "./styles/bottom-panel.css";
import "./styles/tasks.css";
import "./styles/settings.css";
import "./styles/shared.css";
import "./styles/responsive.css";
import App from "./App.jsx";

ReactDOM.createRoot(document.getElementById("app")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
