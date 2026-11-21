// Perfil profissional: o que a IA pode usar. Só fatos reais, revisados por você.
import { api } from "../api.js";
import { listEditor, simpleForm } from "../forms.js";
import { el, toast } from "../ui.js";

const SENIORITY = [
  ["intern", "Estágio"],
  ["junior", "Júnior"],
  ["mid", "Pleno"],
  ["senior", "Sênior"],
  ["specialist", "Especialista"],
  ["lead", "Liderança"],
];
const LEVELS = [
  ["beginner", "Iniciante"],
  ["intermediate", "Intermediário"],
  ["advanced", "Avançado"],
  ["expert", "Especialista"],
];
const PROFICIENCY = [
  ["basic", "Básico"],
  ["intermediate", "Intermediário"],
  ["advanced", "Avançado"],
  ["fluent", "Fluente"],
  ["native", "Nativo"],
];
const WORK_MODELS = [
  ["remote", "Remoto"],
  ["hybrid", "Híbrido"],
  ["onsite", "Presencial"],
];

const BASIC_FIELDS = [
  { name: "full_name", label: "Nome completo" },
  { name: "contact_email", label: "E-mail de contato", type: "email" },
  { name: "phone", label: "Telefone", placeholder: "+55 11 91234-5678" },
  { name: "current_title", label: "Cargo atual" },
  { name: "seniority", label: "Senioridade", type: "select", options: SENIORITY, optional: true },
  { name: "desired_titles", label: "Cargos desejados", type: "list", placeholder: "Separados por vírgula" },
  { name: "city", label: "Cidade" },
  { name: "state", label: "Estado" },
  { name: "country", label: "País" },
  { name: "accepted_locations", label: "Localizações aceitas", type: "list", placeholder: "Ex.: São Paulo, Campinas" },
  { name: "salary_expectation_min", label: "Pretensão salarial mínima", type: "number" },
  { name: "salary_currency", label: "Moeda", placeholder: "BRL" },
  { name: "availability", label: "Disponibilidade", placeholder: "Ex.: Imediata" },
  { name: "linkedin_url", label: "LinkedIn", type: "url" },
  { name: "github_url", label: "GitHub", type: "url" },
  { name: "portfolio_url", label: "Portfólio", type: "url" },
  { name: "summary", label: "Resumo profissional", type: "textarea", rows: 4 },
];

export async function renderProfile(container) {
  let profile = {};
  try {
    profile = await api("/profile");
  } catch (error) {
    if (error.status !== 404) throw error; // 404 = perfil ainda não cadastrado
  }

  const basics = simpleForm(BASIC_FIELDS, profile);
  const workModels = Object.fromEntries(
    WORK_MODELS.map(([value, label]) => {
      const box = el("input", { type: "checkbox" });
      box.checked = (profile.desired_work_models || []).includes(value);
      return [value, { box, label }];
    }),
  );
  const skills = listEditor(
    "Competências",
    [
      { name: "name", label: "Nome" },
      {
        name: "category",
        label: "Tipo",
        type: "select",
        options: [
          ["technology", "Tecnologia"],
          ["technical", "Técnica"],
          ["behavioral", "Comportamental"],
        ],
      },
      { name: "level", label: "Nível", type: "select", options: LEVELS, optional: true },
      { name: "years_of_experience", label: "Anos", type: "number" },
    ],
    profile.skills || [],
    { category: "technology" },
  );
  const languages = listEditor(
    "Idiomas",
    [
      { name: "name", label: "Idioma" },
      { name: "proficiency", label: "Nível", type: "select", options: PROFICIENCY },
    ],
    profile.languages || [],
    { proficiency: "intermediate" },
  );
  const experiences = listEditor(
    "Experiências",
    [
      { name: "company", label: "Empresa" },
      { name: "title", label: "Cargo" },
      { name: "start_date", label: "Início", type: "date" },
      { name: "end_date", label: "Fim", type: "date" },
      { name: "is_current", label: "Atual", type: "checkbox" },
      { name: "technologies", label: "Tecnologias", type: "list" },
      { name: "description", label: "Descrição", type: "textarea" },
    ],
    profile.experiences || [],
  );
  const educations = listEditor(
    "Formação",
    [
      { name: "institution", label: "Instituição" },
      { name: "degree", label: "Curso" },
      { name: "field_of_study", label: "Área" },
      {
        name: "status",
        label: "Situação",
        type: "select",
        options: [
          ["completed", "Concluído"],
          ["in_progress", "Em andamento"],
          ["incomplete", "Incompleto"],
        ],
      },
      { name: "start_date", label: "Início", type: "date" },
      { name: "end_date", label: "Fim", type: "date" },
    ],
    profile.educations || [],
    { status: "completed" },
  );
  const certifications = listEditor(
    "Certificações",
    [
      { name: "name", label: "Nome" },
      { name: "issuer", label: "Emissor" },
      { name: "issued_at", label: "Emissão", type: "date" },
      { name: "credential_url", label: "Link", type: "url" },
    ],
    profile.certifications || [],
  );

  const readProfile = () => ({
    ...basics.read(),
    desired_work_models: Object.entries(workModels)
      .filter(([, { box }]) => box.checked)
      .map(([value]) => value),
    skills: skills.read(),
    languages: languages.read(),
    experiences: experiences.read(),
    educations: educations.read(),
    certifications: certifications.read(),
  });

  // Importação: o rascunho do PDF preenche o formulário; salvar confirma a importação.
  let pendingResumeId = null;
  const resumeInput = el("input", { type: "file", accept: "application/pdf" });
  const resumeNotes = el("ul", { class: "muted" });
  resumeInput.addEventListener("change", async () => {
    const file = resumeInput.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    try {
      const resume = await api("/profile/resumes", { method: "POST", formData });
      const draft = resume.extracted_data?.profile_draft || {};
      basics.fill(draft);
      for (const [editor, key] of [
        [skills, "skills"],
        [languages, "languages"],
        [experiences, "experiences"],
        [educations, "educations"],
        [certifications, "certifications"],
      ]) {
        if (draft[key]?.length) editor.replace(draft[key]);
      }
      resumeNotes.replaceChildren(...(resume.extracted_data?.notes || []).map((note) => el("li", { text: note })));
      pendingResumeId = resume.import_status === "pending_review" ? resume.id : null;
      toast("Rascunho do currículo carregado. Revise e clique em Salvar perfil.", "success");
    } catch (error) {
      toast(error.message, "error");
    }
  });

  const save = el("button", { type: "submit", class: "primary", text: "Salvar perfil" });
  const form = el(
    "form",
    {
      class: "stack",
      onSubmit: async (event) => {
        event.preventDefault();
        save.disabled = true;
        try {
          const data = readProfile();
          if (pendingResumeId) {
            await api(`/profile/resumes/${pendingResumeId}/confirm`, { method: "POST", body: { profile: data } });
            pendingResumeId = null;
          } else {
            await api("/profile", { method: "PUT", body: data });
          }
          toast("Perfil salvo.", "success");
        } catch (error) {
          toast(error.message, "error");
        } finally {
          save.disabled = false;
        }
      },
    },
    el(
      "section",
      { class: "card" },
      el("h2", { text: "Importar currículo (PDF)" }),
      el("p", { class: "muted", text: "O conteúdo extraído só vai para o perfil depois que você revisar e salvar." }),
      resumeInput,
      resumeNotes,
    ),
    el("section", { class: "card" }, el("h2", { text: "Dados gerais" }), basics.node),
    el(
      "section",
      { class: "card" },
      el("h2", { text: "Modalidades aceitas" }),
      el(
        "div",
        { class: "checks" },
        ...Object.values(workModels).map(({ box, label }) => el("label", { class: "checkbox" }, box, label)),
      ),
    ),
    skills.node,
    languages.node,
    experiences.node,
    educations.node,
    certifications.node,
    el("div", { class: "sticky-actions" }, save),
  );

  container.replaceChildren(
    el("h1", { text: "Meu perfil" }),
    el("p", { class: "muted", text: "A IA usa somente o que está aqui: nada é inventado nas análises e nos textos." }),
    form,
  );
}

