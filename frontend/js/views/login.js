import { login } from "../api.js";
import { el, field } from "../ui.js";

export function renderLogin(container, onSuccess) {
  const email = el("input", { type: "email", name: "email", required: true, autocomplete: "username" });
  const password = el("input", {
    type: "password",
    name: "password",
    required: true,
    autocomplete: "current-password",
  });
  const error = el("p", { class: "error", role: "alert" });
  const submit = el("button", { type: "submit", class: "primary", text: "Entrar" });

  const form = el(
    "form",
    {
      class: "card login",
      onSubmit: async (event) => {
        event.preventDefault();
        error.textContent = "";
        submit.disabled = true;
        try {
          await login(email.value, password.value);
          onSuccess();
        } catch (exc) {
          error.textContent = exc.message;
        } finally {
          submit.disabled = false;
        }
      },
    },
    el("h1", { text: "JobPilot AI" }),
    el("p", { class: "muted", text: "Seu agente pessoal de candidaturas." }),
    field("E-mail", email),
    field("Senha", password),
    error,
    submit,
  );
  container.replaceChildren(form);
  email.focus();
}
