import { api } from "../api.js";
import { el, formatDate, runAction } from "../ui.js";

export async function renderNotifications(container, onChange) {
  const data = await api("/notifications", { query: { limit: 100 } });
  const items = data.items.map((notification) => {
    const markRead = el("button", { type: "button", text: "Marcar como lida" });
    markRead.addEventListener("click", async () => {
      const result = await runAction(markRead, () => api(`/notifications/${notification.id}/read`, { method: "PATCH" }));
      if (result) {
        await renderNotifications(container, onChange);
        onChange();
      }
    });
    return el(
      "article",
      { class: `card notification ${notification.read_at ? "read" : "unread"}` },
      el("header", {}, el("h2", { text: notification.title }), el("span", { class: "muted", text: formatDate(notification.created_at) })),
      el("p", { text: notification.body }),
      notification.read_at ? null : markRead,
    );
  });
  container.replaceChildren(
    el("h1", { text: `Notificações (${data.unread} não lidas)` }),
    items.length ? el("div", { class: "stack" }, ...items) : el("p", { class: "muted", text: "Nenhuma notificação." }),
  );
}
