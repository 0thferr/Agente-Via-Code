// Configurações: conta, candidaturas, análise, notificações e e-mail.
import { api } from "../api.js";
import { simpleForm } from "../forms.js";
import { el, field, runAction, safeLink, toast } from "../ui.js";

const ASSISTED_PLATFORMS = [
  ["linkedin", "LinkedIn"],
  ["gupy", "Gupy"],
  ["indeed", "Indeed"],
  ["catho", "Catho"],
  ["vagas_com_br", "Vagas.com.br"],
];
const WEIGHTS = [
  ["skills", "Competências"],
  ["seniority", "Senioridade"],
  ["salary", "Salário"],
  ["location", "Localização"],
  ["work_model", "Modalidade"],
];
const CHANNELS = [
  ["email", "E-mail"],
  ["telegram", "Telegram"],
  ["webhook", "Webhook"],
];

export async function renderSettings(container) {
  const [me, applicationSettings, aiPreferences, notificationPreferences, integrations] = await Promise.all([
    api("/auth/me"),
    api("/applications/settings"),
    api("/ai/preferences"),
    api("/notifications/preferences"),
    api("/integrations"),
  ]);
  container.replaceChildren(
    el("h1", { text: "Configurações" }),
    el(
      "div",
      { class: "stack" },
      accountSection(me),
      applicationsSection(applicationSettings),
      analysisSection(aiPreferences),
      notificationsSection(notificationPreferences, integrations.notification_channels),
      emailSection(integrations.email, () => renderSettings(container)),
    ),
  );
}

function section(title, description, body, onSave) {
  const save = el("button", { type: "button", class: "primary", text: "Salvar" });
  save.addEventListener("click", () => runAction(save, onSave, "Configurações salvas."));
  return el(
    "section",
    { class: "card" },
    el("h2", { text: title }),
    description ? el("p", { class: "muted", text: description }) : null,
    body,
    onSave ? el("div", { class: "actions" }, save) : null,
  );
}

function accountSection(me) {
  const form = simpleForm([{ name: "timezone", label: "Fuso horário (IANA)", placeholder: "America/Sao_Paulo" }], me);
  return section(
    "Conta",
    "O fuso define quando o dia começa para os limites diários, o resumo e o dashboard.",
    form.node,
    () => api("/auth/me", { method: "PATCH", body: form.read() }),
  );
}

function applicationsSection(settings) {
  const form = simpleForm(
    [
      {
        name: "mode",
        label: "Modo",
        type: "select",
        options: [
          ["supervised", "Supervisionado"],
          ["manual", "Manual"],
          ["automatic", "Automático"],
        ],
      },
      { name: "max_daily_submissions", label: "Envios automáticos por dia", type: "number" },
    ],
    settings,
  );
  const limits = Object.fromEntries(
    ASSISTED_PLATFORMS.map(([key]) => {
      const node = el("input", { type: "number", min: 0, max: 100, placeholder: "sem modo assistido" });
      node.value = settings.assisted_daily_limits[key] ?? "";
      return [key, node];
    }),
  );
  return section(
    "Candidaturas",
    "Supervisionado: você aprova antes de cada envio. Manual: o sistema nunca envia. " +
      "Automático: envia sem aprovação só em plataformas autorizadas. Modo assistido: nas " +
      "plataformas abaixo a API prepara o kit e você envia; o limite conta enviadas hoje + kits pendentes.",
    el(
      "div",
      { class: "stack" },
      form.node,
      el(
        "div",
        { class: "form-grid" },
        ...ASSISTED_PLATFORMS.map(([key, label]) => field(`Limite diário — ${label}`, limits[key])),
      ),
    ),
    () => {
      const assisted = Object.fromEntries(
        Object.entries(limits)
          .filter(([, node]) => node.value !== "")
          .map(([key, node]) => [key, Number(node.value)]),
      );
      return api("/applications/settings", {
        method: "PUT",
        body: {
          ...form.read(),
          auto_submit_providers: settings.auto_submit_providers,
          assisted_daily_limits: assisted,
        },
      });
    },
  );
}

function analysisSection(preferences) {
  const form = simpleForm(
    [
      { name: "min_score", label: "Nota mínima (0–100)", type: "number" },
      {
        name: "cover_letter_tone",
        label: "Tom dos textos",
        type: "select",
        options: [
          ["neutral", "Profissional e direto"],
          ["formal", "Formal"],
          ["friendly", "Cordial"],
        ],
      },
    ],
    preferences,
  );
  const weights = simpleForm(
    WEIGHTS.map(([name, label]) => ({ name, label: `Peso: ${label}`, type: "number" })),
    preferences.weights,
  );
  return section(
    "Análise de compatibilidade",
    "Vagas abaixo da nota mínima são descartadas. Os pesos definem a importância de cada critério.",
    el("div", { class: "stack" }, form.node, weights.node),
    () =>
      api("/ai/preferences", {
        method: "PUT",
        body: { ...form.read(), weights: weights.read(), writing_samples: preferences.writing_samples },
      }),
  );
}

function notificationsSection(preferences, available) {
  const boxes = Object.fromEntries(
    CHANNELS.map(([key, label]) => {
      const box = el("input", { type: "checkbox", disabled: available[key] === false && key !== "webhook" });
      box.checked = preferences.channels.includes(key);
      return [key, { box, label }];
    }),
  );
  const form = simpleForm(
    [
      { name: "email_to", label: "E-mail para notificações", type: "email" },
      { name: "telegram_chat_id", label: "Chat ID do Telegram" },
      { name: "webhook_url", label: "URL do webhook (HTTPS)", type: "url" },
    ],
    preferences,
  );
  const secretInfo = el("p", { class: "muted" });
  return section(
    "Notificações",
    "As notificações sempre aparecem no painel. Canais externos desativados não estão configurados no servidor.",
    el(
      "div",
      { class: "stack" },
      el(
        "div",
        { class: "checks" },
        ...Object.values(boxes).map(({ box, label }) => el("label", { class: "checkbox" }, box, label)),
      ),
      form.node,
      secretInfo,
    ),
    async () => {
      const saved = await api("/notifications/preferences", {
        method: "PUT",
        body: {
          ...form.read(),
          channels: Object.entries(boxes)
            .filter(([, { box }]) => box.checked)
            .map(([key]) => key),
          disabled_events: preferences.disabled_events,
          min_interval_minutes: preferences.min_interval_minutes,
        },
      });
      if (saved.webhook_secret) {
        secretInfo.textContent = `Segredo do webhook (guarde agora, ele não será mostrado de novo): ${saved.webhook_secret}`;
      }
      return saved;
    },
  );
}

function emailSection(providers, reload) {
  const labels = { gmail: "Gmail", outlook: "Outlook", simulated: "Caixa simulada" };
  return section(
    "E-mail de recrutamento",
    "Acesso somente leitura: o sistema nunca envia e-mails nem responde recrutadores.",
    el(
      "div",
      { class: "stack" },
      ...providers.map((provider) => {
        const connect = el("button", { type: "button", text: "Conectar", disabled: !provider.configured });
        connect.addEventListener("click", async () => {
          const result = await runAction(connect, () =>
            api(`/integrations/${provider.provider}/connect`, { method: "POST" }),
          );
          if (result?.authorization_url) {
            connect.replaceWith(safeLink(result.authorization_url, "Autorizar acesso ↗"));
          } else if (result?.account) {
            toast("Conta conectada.", "success");
            reload();
          }
        });
        const disconnect = el("button", { type: "button", text: "Desconectar" });
        disconnect.addEventListener("click", async () => {
          const result = await runAction(
            disconnect,
            () => api(`/integrations/${provider.provider}/disconnect`, { method: "DELETE" }),
            "Conta desconectada.",
          );
          if (result !== undefined) reload();
        });
        return el(
          "div",
          { class: "integration" },
          el("strong", { text: labels[provider.provider] || provider.provider }),
          el("span", {
            class: "muted",
            text: provider.configured
              ? provider.accounts.map((account) => account.email_address).join(", ") || "não conectado"
              : "não configurado no servidor",
          }),
          provider.accounts.length ? disconnect : connect,
        );
      }),
    ),
  );
}
