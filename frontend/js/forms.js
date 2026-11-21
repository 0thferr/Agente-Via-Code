// Componentes de formulário reutilizáveis: campos simples e editor de listas.
import { el, field } from "./ui.js";

// Cria um <input>, <select>, <textarea> ou checkbox a partir de uma especificação.
export function input(spec, value) {
  if (spec.type === "select") {
    const node = el(
      "select",
      {},
      ...(spec.optional ? [el("option", { value: "", text: "—" })] : []),
      ...spec.options.map(([optionValue, label]) => el("option", { value: optionValue, text: label })),
    );
    node.value = value ?? "";
    return node;
  }
  if (spec.type === "textarea") {
    const node = el("textarea", { rows: spec.rows || 3 });
    node.value = value ?? "";
    return node;
  }
  if (spec.type === "checkbox") {
    const node = el("input", { type: "checkbox" });
    node.checked = Boolean(value);
    return node;
  }
  const node = el("input", { type: spec.type || "text", placeholder: spec.placeholder || "" });
  node.value = value ?? "";
  return node;
}

// Lê o valor de um campo já convertido para o formato que a API espera.
export function readInput(spec, node) {
  if (spec.type === "checkbox") return node.checked;
  const raw = node.value.trim();
  if (spec.type === "list") return raw ? raw.split(",").map((item) => item.trim()).filter(Boolean) : [];
  if (spec.type === "number") return raw === "" ? null : Number(raw);
  return raw === "" ? null : raw;
}

// Formulário de campos simples: devolve { node, read(), fill(values) }.
export function simpleForm(specs, values = {}) {
  const inputs = Object.fromEntries(
    specs.map((spec) => [spec.name, input(spec, toDisplay(spec, values[spec.name]))]),
  );
  return {
    node: el("div", { class: "form-grid" }, ...specs.map((spec) => field(spec.label, inputs[spec.name]))),
    read: () => Object.fromEntries(specs.map((spec) => [spec.name, readInput(spec, inputs[spec.name])])),
    fill: (newValues) => {
      for (const spec of specs) {
        if (newValues[spec.name] === undefined) continue;
        const node = inputs[spec.name];
        const display = toDisplay(spec, newValues[spec.name]);
        if (spec.type === "checkbox") node.checked = Boolean(display);
        else node.value = display ?? "";
      }
    },
  };
}

// Editor de listas (competências, idiomas, experiências...): uma linha por item.
export function listEditor(title, specs, items = [], emptyItem = {}) {
  const rows = el("div", { class: "list-rows" });
  const entries = [];

  function addRow(item) {
    const inputs = Object.fromEntries(specs.map((spec) => [spec.name, input(spec, toDisplay(spec, item[spec.name]))]));
    const row = el(
      "div",
      { class: "list-row" },
      ...specs.map((spec) => field(spec.label, inputs[spec.name])),
      el("button", {
        type: "button",
        class: "link",
        text: "Remover",
        onClick: () => {
          row.remove();
          entries.splice(entries.indexOf(entry), 1);
        },
      }),
    );
    const entry = { inputs };
    entries.push(entry);
    rows.append(row);
  }

  items.forEach(addRow);
  return {
    node: el(
      "fieldset",
      { class: "card" },
      el("legend", { text: title }),
      rows,
      el("button", { type: "button", text: "Adicionar", onClick: () => addRow(emptyItem) }),
    ),
    read: () =>
      entries.map(({ inputs }) =>
        Object.fromEntries(specs.map((spec) => [spec.name, readInput(spec, inputs[spec.name])])),
      ),
    replace: (newItems) => {
      rows.replaceChildren();
      entries.length = 0;
      newItems.forEach(addRow);
    },
  };
}

function toDisplay(spec, value) {
  if (spec.type === "list") return Array.isArray(value) ? value.join(", ") : value;
  return value;
}
