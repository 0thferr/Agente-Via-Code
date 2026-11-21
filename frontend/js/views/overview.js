import { api } from "../api.js";
import { el } from "../ui.js";

const CARDS = [
  ["jobs_found", "Vagas encontradas"],
  ["jobs_qualified", "Qualificadas"],
  ["applications_awaiting_approval", "Aguardando você"],
  ["applications_submitted", "Candidaturas enviadas"],
  ["interviews", "Entrevistas"],
  ["offers", "Propostas"],
];
const STAGE_LABELS = {
  jobs_found: "Vagas encontradas",
  jobs_analyzed: "Analisadas",
  jobs_qualified: "Qualificadas",
  applications_created: "Candidaturas criadas",
  applications_submitted: "Enviadas",
  assessment: "Teste técnico",
  interview: "Entrevista",
  offer: "Proposta",
  hired: "Contratação",
};

export async function renderOverview(container) {
  const [overview, funnel] = await Promise.all([api("/dashboard/overview"), api("/dashboard/funnel")]);
  const percent = (value) => (value === null ? "—" : `${Math.round(value * 100)}%`);

  container.replaceChildren(
    el("h1", { text: "Visão geral (últimos 30 dias)" }),
    el(
      "section",
      { class: "cards" },
      ...CARDS.map(([key, label]) =>
        el("div", { class: "card stat" }, el("strong", { text: String(overview[key]) }), el("span", { text: label })),
      ),
      el(
        "div",
        { class: "card stat" },
        el("strong", { text: overview.average_score ?? "—" }),
        el("span", { text: "Nota média" }),
      ),
      el(
        "div",
        { class: "card stat" },
        el("strong", { text: percent(overview.interview_rate) }),
        el("span", { text: "Taxa de entrevista" }),
      ),
    ),
    el("h2", { text: "Funil" }),
    el(
      "table",
      { class: "table" },
      el("thead", {}, el("tr", {}, el("th", { text: "Etapa" }), el("th", { text: "Total" }), el("th", { text: "Conversão" }))),
      el(
        "tbody",
        {},
        ...funnel.stages.map((stage) =>
          el(
            "tr",
            {},
            el("td", { text: STAGE_LABELS[stage.stage] || stage.stage }),
            el("td", { text: String(stage.count) }),
            el("td", { text: percent(stage.rate_from_previous) }),
          ),
        ),
      ),
    ),
  );
}
