// Automações: rotinas que o worker executa (buscar, analisar, preparar, e-mails...).
import { api } from "../api.js";
import { simpleForm } from "../forms.js";
import { el, formatDate, modal, runAction, toast } from "../ui.js";

const TASKS = [
  ["discover_jobs", "Buscar vagas"],
  ["analyze_jobs", "Analisar vagas"],
  ["prepare_applications", "Preparar candidaturas"],
  ["submit_applications", "Enviar candidaturas autorizadas"],
  ["check_emails", "Verificar e-mails"],
  ["check_deadlines", "Avisar prazos"],
  ["daily_summary", "Resumo diário"],
];
const TASK_LABELS = Object.fromEntries(TASKS);
const DAYS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"];
const RUN_STATUS = { pending: "Na fila", running: "Executando", succeeded: "Concluída", failed: "Falhou" };

export async function renderAutomations(container) {
  const [rules, executions] = await Promise.all([
    api("/automations"),
    api("/automations/executions", { query: { limit: 20 } }),
  ]);
  const reload = () => renderAutomations(container);
  container.replaceChildren(
    el(
      "div",
      { class: "toolbar" },
      el("h1", { text: "Automações" }),
      el("button", { type: "button", class: "primary", text: "Nova automação", onClick: () => openCreate(reload) }),
    ),
    el("p", { class: "muted", text: "As automações rodam no worker (processo separado). Sem o worker ligado, elas ficam na fila." }),
    rules.length
      ? el("div", { class: "stack" }, ...rules.map((rule) => ruleCard(rule, reload)))
      : el("p", { class: "muted", text: "Nenhuma automação ainda." }),
    el("h2", { text: "Últimas execuções" }),
    executionsTable(executions.items),
  );
}

function schedule(rule) {
  if (rule.interval_minutes) return `a cada ${rule.interval_minutes} min`;
  const days = rule.days_of_week.length ? rule.days_of_week.map((day) => DAYS[day]).join(", ") : "todos os dias";
  return `${days} às ${rule.time_of_day} (${rule.timezone})`;
}

function ruleCard(rule, reload) {
  const toggle = el("button", { type: "button", text: rule.is_enabled ? "Desativar" : "Ativar" });
  toggle.addEventListener("click", async () => {
    const result = await runAction(toggle, () =>
      api(`/automations/${rule.id}`, { method: "PATCH", body: { is_enabled: !rule.is_enabled } }),
    );
    if (result) reload();
  });
  const run = el("button", { type: "button", text: "Executar agora", disabled: !rule.is_enabled });
  run.addEventListener("click", async () => {
    const result = await runAction(
      run,
      () => api(`/automations/${rule.id}/run`, { method: "POST" }),
      "Execução enfileirada.",
    );
    if (result) reload();
  });
  return el(
    "article",
    { class: `card ${rule.is_enabled ? "" : "disabled"}` },
    el(
      "header",
      { class: "toolbar" },
      el("h2", { text: rule.name }),
      el("span", { class: "badge", text: rule.is_enabled ? "Ativa" : "Desativada" }),
    ),
    el("p", { text: `${TASK_LABELS[rule.task_type] || rule.task_type} · ${schedule(rule)}` }),
    el("p", { class: "muted", text: `Próxima execução: ${rule.is_enabled ? formatDate(rule.next_run_at) : "—"}` }),
    el("div", { class: "actions" }, run, toggle),
  );
}

function executionsTable(items) {
  if (!items.length) return el("p", { class: "muted", text: "Nenhuma execução ainda." });
  return el(
    "table",
    { class: "table" },
    el("thead", {}, el("tr", {}, ...["Tarefa", "Status", "Tentativas", "Resultado", "Quando"].map((title) => el("th", { text: title })))),
    el(
      "tbody",
      {},
      ...items.map((job) => {
        const lastLog = job.logs.at(-1);
        const result = job.last_error || (lastLog ? JSON.stringify(lastLog.summary) : "—");
        return el(
          "tr",
          {},
          el("td", { text: TASK_LABELS[job.task_type] || job.task_type }),
          el("td", { text: RUN_STATUS[job.status] || job.status }),
          el("td", { text: `${job.attempts}/${job.max_attempts}` }),
          el("td", { class: "result", text: result }),
          el("td", { text: formatDate(job.finished_at || job.scheduled_for) }),
        );
      }),
    ),
  );
}

function openCreate(reload) {
  const form = simpleForm([
    { name: "name", label: "Nome" },
    { name: "task_type", label: "Tarefa", type: "select", options: TASKS },
    {
      name: "kind",
      label: "Agendamento",
      type: "select",
      options: [
        ["time", "Horário fixo"],
        ["interval", "Intervalo"],
      ],
    },
    { name: "time_of_day", label: "Horário (HH:MM)", placeholder: "08:00" },
    { name: "days_of_week", label: "Dias (0=seg … 6=dom, vazio = todos)", type: "list", placeholder: "0, 1, 2, 3, 4" },
    { name: "timezone", label: "Fuso horário", placeholder: "America/Sao_Paulo" },
    { name: "interval_minutes", label: "Intervalo em minutos (mín. 15)", type: "number" },
  ]);
  form.fill({ kind: "time", time_of_day: "08:00", timezone: "America/Sao_Paulo", interval_minutes: "" });
  modal("Nova automação", form.node, [
    {
      label: "Criar",
      primary: true,
      onClick: async (close) => {
        const values = form.read();
        const body = {
          name: values.name,
          task_type: values.task_type,
          timezone: values.timezone || "America/Sao_Paulo",
          ...(values.kind === "interval"
            ? { interval_minutes: values.interval_minutes }
            : { time_of_day: values.time_of_day, days_of_week: values.days_of_week.map(Number) }),
        };
        try {
          await api("/automations", { method: "POST", body });
          toast("Automação criada.", "success");
          close();
          reload();
        } catch (error) {
          toast(error.message, "error");
        }
      },
    },
  ]);
}
