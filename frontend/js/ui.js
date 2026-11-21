// Utilitários de interface.
//
// Regra de segurança: dados vindos da API (descrições de vagas, e-mails, textos) são
// inseridos SEMPRE como texto (textContent), nunca como HTML. Assim, um conteúdo
// malicioso em uma vaga não vira código executando no navegador (XSS).

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

// Só aceita links http(s): um "javascript:..." vindo de uma vaga não pode virar link.
export function safeLink(url, label) {
  const isHttp = /^https?:\/\//i.test(url || "");
  return isHttp
    ? el("a", { href: url, target: "_blank", rel: "noopener noreferrer", text: label })
    : el("span", { text: label });
}

export function toast(message, kind = "info") {
  const container = document.getElementById("toasts");
  const node = el("div", { class: `toast toast-${kind}`, role: "status", text: message });
  container.append(node);
  setTimeout(() => node.remove(), 6000);
}

export function formatDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

export async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copiado!", "success");
  } catch {
    toast("Não foi possível copiar. Selecione o texto e copie manualmente.", "error");
  }
}

// Executa uma ação da API mostrando erro amigável e evitando cliques repetidos.
export async function runAction(button, action, successMessage) {
  button.disabled = true;
  try {
    const result = await action();
    if (successMessage) toast(successMessage, "success");
    return result;
  } catch (error) {
    toast(error.message, "error");
    return undefined;
  } finally {
    button.disabled = false;
  }
}

export function modal(title, body, actions = []) {
  const dialog = el("dialog", { class: "modal", "aria-label": title });
  const close = () => {
    dialog.close();
    dialog.remove();
  };
  dialog.append(
    el("h2", { text: title }),
    body,
    el(
      "div",
      { class: "modal-actions" },
      ...actions.map(({ label, onClick, primary }) =>
        el("button", {
          type: "button",
          class: primary ? "primary" : "",
          text: label,
          onClick: () => onClick(close),
        }),
      ),
      el("button", { type: "button", text: "Fechar", onClick: close }),
    ),
  );
  dialog.addEventListener("cancel", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  return close;
}

export function field(label, input) {
  return el("label", { class: "field" }, el("span", { text: label }), input);
}

export const STATUS_LABELS = {
  discovered: "Descoberta",
  analyzing: "Analisando",
  rejected: "Descartada",
  qualified: "Qualificada",
  awaiting_approval: "Aguardando aprovação",
  ready_to_apply: "Pronta para enviar",
  applying: "Enviando",
  applied: "Enviada",
  assessment: "Teste técnico",
  interview: "Entrevista",
  offer: "Proposta",
  hired: "Contratada",
  closed: "Encerrada",
  error: "Erro no envio",
};

export function statusBadge(status) {
  return el("span", { class: `badge badge-${status}`, text: STATUS_LABELS[status] || status });
}

export function scoreBadge(score, recommendation) {
  if (score === null || score === undefined) return el("span", { class: "muted", text: "—" });
  const kind = recommendation === "qualified" ? "good" : "bad";
  return el("span", { class: `score score-${kind}`, title: recommendation, text: String(score) });
}
