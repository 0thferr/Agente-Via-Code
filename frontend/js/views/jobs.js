import { api } from "../api.js";
import { clear, el, field, modal, runAction, safeLink, scoreBadge, toast } from "../ui.js";

export async function renderJobs(container) {
  const search = el("input", { type: "search", placeholder: "Buscar por cargo ou empresa" });
  const list = el("div");

  async function load() {
    const data = await api("/jobs", { query: { q: search.value.trim(), limit: 100 } });
    clear(list).append(jobsTable(data.items, load));
  }

  container.replaceChildren(
    el(
      "div",
      { class: "toolbar" },
      el("h1", { text: "Vagas" }),
      el("button", { type: "button", class: "primary", text: "Importar vaga", onClick: () => openImport(load) }),
    ),
    el(
      "form",
      { class: "filters", onSubmit: (event) => (event.preventDefault(), load()) },
      search,
      el("button", { type: "submit", text: "Buscar" }),
    ),
    list,
  );
  await load();
}

function jobsTable(jobs, reload) {
  if (!jobs.length) return el("p", { class: "muted", text: "Nenhuma vaga ainda. Importe uma vaga ou crie uma automação de busca." });
  return el(
    "table",
    { class: "table" },
    el(
      "thead",
      {},
      el("tr", {}, ...["Vaga", "Empresa", "Plataforma", "Nota", "Ações"].map((title) => el("th", { text: title }))),
    ),
    el(
      "tbody",
      {},
      ...jobs.map((job) => {
        const analyze = el("button", { type: "button", text: "Analisar" });
        analyze.addEventListener("click", async () => {
          const analysis = await runAction(analyze, () => api(`/jobs/${job.id}/analyze`, { method: "POST" }));
          if (analysis) showAnalysis(job, analysis, reload);
        });
        const apply = el("button", { type: "button", text: "Candidatar" });
        apply.addEventListener("click", async () => {
          const created = await runAction(
            apply,
            () => api("/applications", { method: "POST", body: { job_id: job.id } }),
            "Candidatura criada. Prepare-a na aba Candidaturas.",
          );
          if (created) window.location.hash = "#/candidaturas";
        });
        return el(
          "tr",
          {},
          el("td", {}, safeLink(job.url, job.title)),
          el("td", { text: job.company }),
          el("td", { text: job.provider_key }),
          el("td", {}, scoreBadge(job.latest_score, job.latest_recommendation)),
          el("td", { class: "actions" }, analyze, apply),
        );
      }),
    ),
  );
}

function showAnalysis(job, analysis, reload) {
  const list = (title, items) =>
    items.length ? el("div", {}, el("h3", { text: title }), el("ul", {}, ...items.map((item) => el("li", { text: item })))) : null;
  modal(
    `Análise: ${job.title}`,
    el(
      "div",
      {},
      el("p", {}, "Nota: ", scoreBadge(analysis.score, analysis.recommendation), ` · confiança ${Math.round(analysis.confidence * 100)}%`),
      list("Motivos", analysis.decision_reasons),
      list("Competências atendidas", analysis.matched_skills),
      list("Requisitos obrigatórios não atendidos", analysis.unmet_mandatory_requirements),
      list("Avisos", analysis.warnings),
    ),
  );
  reload();
}

function openImport(reload) {
  const inputs = {
    url: el("input", { type: "url", required: true, placeholder: "https://www.linkedin.com/jobs/view/..." }),
    title: el("input", { required: true }),
    company: el("input", { required: true }),
    work_model: el(
      "select",
      {},
      el("option", { value: "", text: "Não informado" }),
      el("option", { value: "remote", text: "Remoto" }),
      el("option", { value: "hybrid", text: "Híbrido" }),
      el("option", { value: "onsite", text: "Presencial" }),
    ),
    requirements: el("textarea", { rows: 3, placeholder: "Um por linha. Ex.: Python\n3 anos de experiência" }),
    technologies: el("input", { placeholder: "Separadas por vírgula. Ex.: Docker, PostgreSQL" }),
    description: el("textarea", { rows: 6, placeholder: "Cole aqui a descrição da vaga" }),
  };
  const lines = (text, separator) =>
    text
      .split(separator)
      .map((item) => item.trim())
      .filter(Boolean);
  modal(
    "Importar vaga",
    el(
      "div",
      { class: "form" },
      field("Link da vaga", inputs.url),
      field("Cargo", inputs.title),
      field("Empresa", inputs.company),
      field("Modalidade", inputs.work_model),
      field("Requisitos obrigatórios", inputs.requirements),
      field("Tecnologias desejáveis", inputs.technologies),
      el("p", {
        class: "muted",
        text: "Sem requisitos listados, a análise simulada (sem IA) não tem o que avaliar e fica pendente.",
      }),
      field("Descrição", inputs.description),
    ),
    [
      {
        label: "Importar",
        primary: true,
        onClick: async (close) => {
          const body = {
            url: inputs.url.value.trim(),
            title: inputs.title.value.trim(),
            company: inputs.company.value.trim(),
            description: inputs.description.value,
            work_model: inputs.work_model.value || null,
            requirements: lines(inputs.requirements.value, "\n"),
            technologies: lines(inputs.technologies.value, ","),
          };
          try {
            const result = await api("/jobs/import", { method: "POST", body });
            toast(result.created ? "Vaga importada." : "Essa vaga já estava cadastrada.", "success");
            close();
            reload();
          } catch (error) {
            toast(error.message, "error");
          }
        },
      },
    ],
  );
}
