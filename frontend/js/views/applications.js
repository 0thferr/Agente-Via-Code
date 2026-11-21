import { api } from "../api.js";
import {
  STATUS_LABELS,
  clear,
  copyToClipboard,
  el,
  field,
  formatDate,
  modal,
  runAction,
  safeLink,
  statusBadge,
  toast,
} from "../ui.js";

const PROGRESS = ["assessment", "interview", "offer", "hired", "closed"];

export async function renderApplications(container) {
  const status = el(
    "select",
    { "aria-label": "Filtrar por status" },
    el("option", { value: "", text: "Todos os status" }),
    ...Object.entries(STATUS_LABELS).map(([value, label]) => el("option", { value, text: label })),
  );
  const list = el("div");

  async function load() {
    const [applications, jobs] = await Promise.all([
      api("/applications", { query: { status: status.value, limit: 100 } }),
      api("/jobs", { query: { limit: 100 } }),
    ]);
    const jobsById = Object.fromEntries(jobs.items.map((job) => [job.id, job]));
    clear(list).append(applicationsList(applications.items, jobsById, load));
  }

  status.addEventListener("change", load);
  container.replaceChildren(el("div", { class: "toolbar" }, el("h1", { text: "Candidaturas" }), status), list);
  await load();
}

function applicationsList(applications, jobsById, reload) {
  if (!applications.length) return el("p", { class: "muted", text: "Nenhuma candidatura. Crie uma a partir da aba Vagas." });
  return el(
    "div",
    { class: "stack" },
    ...applications.map((application) => applicationCard(application, jobsById[application.job_id], reload)),
  );
}

function applicationCard(application, job, reload) {
  const title = job ? `${job.title} — ${job.company}` : "Vaga";
  return el(
    "article",
    { class: "card application" },
    el(
      "header",
      {},
      el("h2", {}, job ? safeLink(job.url, title) : title),
      statusBadge(application.status),
      application.compatibility_score !== null ? el("span", { class: "muted", text: `nota ${application.compatibility_score}` }) : null,
    ),
    application.pending_questions.length
      ? el("p", { class: "warning", text: `Perguntas pendentes: ${application.pending_questions.map((q) => q.label).join(", ")}` })
      : null,
    application.last_error ? el("p", { class: "error", text: application.last_error }) : null,
    application.external_confirmation_id
      ? el("p", { class: "muted", text: `Confirmação: ${application.external_confirmation_id} (${formatDate(application.submitted_at)})` })
      : null,
    el("div", { class: "actions" }, ...actionsFor(application, reload)),
  );
}

function actionsFor(application, reload) {
  const id = application.id;
  const button = (label, handler, primary = false) => {
    const node = el("button", { type: "button", class: primary ? "primary" : "", text: label });
    node.addEventListener("click", () => handler(node));
    return node;
  };
  const call = (node, path, body, message) =>
    runAction(node, () => api(`/applications/${id}${path}`, { method: "POST", body }), message).then(
      (result) => result && reload(),
    );

  const actions = [];
  const status = application.status;
  if (["discovered", "rejected"].includes(status)) {
    actions.push(button("Preparar", (node) => call(node, "/prepare", {}, "Candidatura preparada."), true));
  }
  if (application.pending_questions.length) {
    actions.push(button("Responder perguntas", () => openAnswers(application, reload), true));
  }
  if (["awaiting_approval", "ready_to_apply"].includes(status)) {
    actions.push(button("Ver kit", () => openKit(application, reload)));
  }
  if (status === "awaiting_approval" && !application.pending_questions.length) {
    actions.push(button("Aprovar", (node) => call(node, "/approve", {}, "Aprovada."), true));
  }
  if (["ready_to_apply", "error"].includes(status)) {
    actions.push(button(status === "error" ? "Tentar enviar de novo" : "Enviar", (node) => call(node, "/submit", undefined, "Enviada!"), true));
  }
  if (["awaiting_approval", "ready_to_apply", "error"].includes(status)) {
    actions.push(button("Já me candidatei", () => openMarkApplied(application, reload)));
  }
  if (["applied", "assessment", "interview", "offer"].includes(status)) {
    actions.push(progressSelect(application, reload));
  }
  if (!["hired", "closed", "applied", "assessment", "interview", "offer", "applying"].includes(status)) {
    actions.push(button("Recusar", () => openDecline(application, reload)));
  }
  actions.push(button("Linha do tempo", () => openTimeline(application)));
  return actions;
}

async function openKit(application, reload) {
  let kit;
  try {
    kit = await api(`/applications/${application.id}/kit`);
  } catch (error) {
    toast(error.message, "error");
    return;
  }
  const copyRow = (label, value) =>
    el(
      "div",
      { class: "copy-row" },
      el("span", { class: "muted", text: label }),
      el("span", { class: "value", text: value }),
      el("button", { type: "button", text: "Copiar", onClick: () => copyToClipboard(value) }),
    );
  const acknowledge = el("input", { type: "checkbox" });
  modal(
    `Kit: ${kit.job_title} — ${kit.company}`,
    el(
      "div",
      { class: "kit" },
      el("p", {}, safeLink(kit.job_url, "Abrir a vaga na plataforma ↗")),
      kit.quota
        ? el("p", { class: "muted", text: `Cota de hoje em ${kit.quota.platform}: ${kit.quota.remaining} de ${kit.quota.limit} disponíveis.` })
        : null,
      el("h3", { text: "Respostas do formulário" }),
      ...kit.answers.map((answer) => copyRow(answer.label, answer.value)),
      el("h3", { text: "Texto de apresentação" }),
      el("pre", { class: "letter", text: kit.cover_letter || "—" }),
      kit.cover_letter ? el("button", { type: "button", text: "Copiar texto", onClick: () => copyToClipboard(kit.cover_letter) }) : null,
      kit.cover_letter_warnings.length
        ? el("div", { class: "warning" }, el("strong", { text: "Revise antes de usar:" }), el("ul", {}, ...kit.cover_letter_warnings.map((w) => el("li", { text: w }))))
        : null,
      kit.resume_download_path ? el("p", { class: "muted", text: `Currículo: ${kit.resume_filename}` }) : el("p", { class: "warning", text: "Nenhum currículo enviado." }),
      application.status === "awaiting_approval" && kit.cover_letter_warnings.length
        ? el("label", { class: "checkbox" }, acknowledge, " Revisei os alertas do texto")
        : null,
      el("p", {
        class: "muted",
        text: "Depois de enviar na plataforma, volte aqui e clique em “Já me candidatei”.",
      }),
    ),
    application.status === "awaiting_approval"
      ? [
          {
            label: "Aprovar",
            primary: true,
            onClick: async (close) => {
              try {
                await api(`/applications/${application.id}/approve`, {
                  method: "POST",
                  body: { acknowledge_warnings: acknowledge.checked },
                });
                toast("Aprovada.", "success");
                close();
                reload();
              } catch (error) {
                toast(error.message, "error");
              }
            },
          },
        ]
      : [],
  );
}

function openAnswers(application, reload) {
  const inputs = Object.fromEntries(application.pending_questions.map((question) => [question.id, el("input", { required: true })]));
  modal(
    "Perguntas do formulário",
    el(
      "div",
      { class: "form" },
      el("p", { class: "muted", text: "Seu perfil não tem estas respostas. Elas valem só para esta candidatura." }),
      ...application.pending_questions.map((question) => field(question.label, inputs[question.id])),
    ),
    [
      {
        label: "Salvar respostas",
        primary: true,
        onClick: async (close) => {
          const answers = Object.fromEntries(
            Object.entries(inputs)
              .map(([id, input]) => [id, input.value.trim()])
              .filter(([, value]) => value),
          );
          try {
            await api(`/applications/${application.id}/answers`, { method: "POST", body: { answers } });
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

function openMarkApplied(application, reload) {
  const confirmation = el("input", { required: true, placeholder: "Ex.: protocolo, assunto do e-mail de confirmação" });
  modal(
    "Registrar candidatura enviada",
    el("div", { class: "form" }, field("Comprovante da plataforma", confirmation)),
    [
      {
        label: "Registrar",
        primary: true,
        onClick: async (close) => {
          try {
            await api(`/applications/${application.id}/mark-applied`, {
              method: "POST",
              body: { confirmation: confirmation.value.trim() },
            });
            toast("Candidatura registrada como enviada.", "success");
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

function openDecline(application, reload) {
  const reason = el("input", { required: true, placeholder: "Ex.: salário abaixo do esperado" });
  modal("Recusar candidatura", el("div", { class: "form" }, field("Motivo", reason)), [
    {
      label: "Recusar",
      onClick: async (close) => {
        try {
          await api(`/applications/${application.id}/reject`, { method: "POST", body: { reason: reason.value.trim() } });
          close();
          reload();
        } catch (error) {
          toast(error.message, "error");
        }
      },
    },
  ]);
}

function progressSelect(application, reload) {
  const select = el(
    "select",
    { "aria-label": "Atualizar etapa" },
    el("option", { value: "", text: "Atualizar etapa…" }),
    ...PROGRESS.map((status) => el("option", { value: status, text: STATUS_LABELS[status] })),
  );
  select.addEventListener("change", async () => {
    if (!select.value) return;
    try {
      await api(`/applications/${application.id}/progress`, { method: "POST", body: { status: select.value } });
      toast("Etapa atualizada.", "success");
      reload();
    } catch (error) {
      toast(error.message, "error");
      select.value = "";
    }
  });
  return select;
}

async function openTimeline(application) {
  try {
    const events = await api(`/applications/${application.id}/events`);
    modal(
      "Linha do tempo",
      el(
        "ol",
        { class: "timeline" },
        ...events.map((event) =>
          el(
            "li",
            {},
            el("span", { class: "muted", text: formatDate(event.created_at) }),
            " ",
            event.to_status ? statusBadge(event.to_status) : null,
            " ",
            el("span", { text: event.event_type.replaceAll("_", " ") }),
          ),
        ),
      ),
    );
  } catch (error) {
    toast(error.message, "error");
  }
}
