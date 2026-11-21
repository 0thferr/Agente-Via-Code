// Ponto de entrada do dashboard: roteamento por hash (#/vagas...) e navegação.
import { api, isLoggedIn, logout } from "./api.js";
import { el, toast } from "./ui.js";
import { renderApplications } from "./views/applications.js";
import { renderAutomations } from "./views/automations.js";
import { renderJobs } from "./views/jobs.js";
import { renderLogin } from "./views/login.js";
import { renderNotifications } from "./views/notifications.js";
import { renderOverview } from "./views/overview.js";
import { renderProfile } from "./views/profile.js";
import { renderSettings } from "./views/settings.js";

const ROUTES = {
  "#/": { label: "Visão geral", render: renderOverview },
  "#/vagas": { label: "Vagas", render: renderJobs },
  "#/candidaturas": { label: "Candidaturas", render: renderApplications },
  "#/notificacoes": { label: "Notificações", render: (container) => renderNotifications(container, updateUnreadCount) },
  "#/automacoes": { label: "Automações", render: renderAutomations },
  "#/perfil": { label: "Perfil", render: renderProfile },
  "#/configuracoes": { label: "Configurações", render: renderSettings },
};

const main = document.getElementById("main");
const topbar = document.getElementById("topbar");
const nav = document.getElementById("nav");

async function render() {
  if (!isLoggedIn()) {
    topbar.hidden = true;
    renderLogin(main, () => {
      window.location.hash = "#/";
      render();
    });
    return;
  }
  topbar.hidden = false;
  const hash = ROUTES[window.location.hash] ? window.location.hash : "#/";
  renderNav(hash);
  main.replaceChildren(el("p", { class: "muted", text: "Carregando…" }));
  try {
    await ROUTES[hash].render(main);
    main.focus();
  } catch (error) {
    if (!isLoggedIn()) return render();
    main.replaceChildren(el("p", { class: "error", text: error.message }));
  }
  updateUnreadCount();
}

function renderNav(current) {
  nav.replaceChildren(
    ...Object.entries(ROUTES).map(([hash, route]) =>
      el("a", { href: hash, class: hash === current ? "active" : "", "aria-current": hash === current ? "page" : false, id: `nav-${hash.slice(2) || "inicio"}`, text: route.label }),
    ),
  );
}

async function updateUnreadCount() {
  try {
    const data = await api("/notifications", { query: { unread_only: true, limit: 1 } });
    const link = document.getElementById("nav-notificacoes");
    if (link) link.textContent = data.unread ? `Notificações (${data.unread})` : "Notificações";
  } catch {
    // Contador é só informativo: uma falha aqui não deve atrapalhar a tela.
  }
}

document.getElementById("logout").addEventListener("click", () => {
  logout();
  toast("Você saiu.", "info");
  render();
});
window.addEventListener("hashchange", render);
window.addEventListener("jobpilot:logout", () => {
  toast("Sua sessão expirou. Entre novamente.", "info");
  render();
});

render();
