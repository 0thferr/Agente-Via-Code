// Cliente da API do JobPilot: autenticação, renovação de token e tratamento de erros.
//
// O access token fica só em memória (some ao fechar a aba). O refresh token fica no
// sessionStorage para sobreviver a um recarregamento da página na mesma aba.
// Em produção, o ideal é um cookie httpOnly emitido pelo servidor (fora do alcance
// do JavaScript); isso exigiria autenticação por cookie no backend.

const API_BASE = "/api/v1";
const REFRESH_KEY = "jobpilot.refresh_token";

let accessToken = null;
let refreshInFlight = null;

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export function isLoggedIn() {
  return accessToken !== null || readRefreshToken() !== null;
}

export async function login(email, password) {
  const tokens = await request("/auth/login", { method: "POST", body: { email, password }, auth: false });
  storeTokens(tokens);
}

export function logout() {
  accessToken = null;
  try {
    sessionStorage.removeItem(REFRESH_KEY);
  } catch {
    // Armazenamento indisponível (ex.: modo privado restrito): nada a remover.
  }
}

export async function api(path, options = {}) {
  // Página recarregada: o access token (só em memória) sumiu, mas o refresh token
  // ainda está na aba. Renova antes, em vez de falhar com 401 e tentar de novo.
  if (options.auth !== false && accessToken === null && readRefreshToken()) {
    await refreshTokens();
  }
  try {
    return await request(path, options);
  } catch (error) {
    // Access token expirado: renova uma vez e repete a requisição.
    if (error.status === 401 && options.auth !== false && readRefreshToken()) {
      await refreshTokens();
      return request(path, options);
    }
    throw error;
  }
}

// `formData` é usado em uploads de arquivo: o navegador define o Content-Type
// (multipart com boundary) sozinho, por isso não o informamos.
async function request(path, { method = "GET", body, formData, query, auth = true } = {}) {
  const url = new URL(API_BASE + path, window.location.origin);
  for (const [key, value] of Object.entries(query || {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  }
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth && accessToken) headers.Authorization = `Bearer ${accessToken}`;

  const response = await fetch(url, {
    method,
    headers,
    body: formData ?? (body === undefined ? undefined : JSON.stringify(body)),
  });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(errorMessage(data, response.status), response.status);
  return data;
}

async function refreshTokens() {
  // Várias requisições podem expirar juntas: todas esperam a mesma renovação.
  refreshInFlight ??= (async () => {
    try {
      const tokens = await request("/auth/refresh", {
        method: "POST",
        body: { refresh_token: readRefreshToken() },
        auth: false,
      });
      storeTokens(tokens);
    } catch (error) {
      logout();
      window.dispatchEvent(new Event("jobpilot:logout"));
      throw error;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

function storeTokens(tokens) {
  accessToken = tokens.access_token;
  try {
    sessionStorage.setItem(REFRESH_KEY, tokens.refresh_token);
  } catch {
    // Sem armazenamento: a sessão dura só enquanto a página estiver aberta.
  }
}

function readRefreshToken() {
  try {
    return sessionStorage.getItem(REFRESH_KEY);
  } catch {
    return null;
  }
}

function errorMessage(data, status) {
  if (data?.error?.message) return data.error.message;
  // Erros de validação do FastAPI: lista de problemas por campo.
  if (Array.isArray(data?.detail)) {
    return data.detail.map((item) => item.msg.replace(/^Value error, /, "")).join(" · ");
  }
  return `Erro inesperado (HTTP ${status}).`;
}
