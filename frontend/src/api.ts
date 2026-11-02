export type Role = "operator" | "supervisor" | "admin";

export type User = { id: string; email: string; name: string; role: Role; active: boolean };

export type Batch = {
  id: string;
  file_name: string;
  stamp_code_from_name: string | null;
  uploaded_at: string | null;
  status: string;
  page_count: number;
  card_count: number;
  ai_cost: number;
  progress: { stage?: string; percent?: number; message?: string; page?: number; pages?: number };
  error: string | null;
};

export type Source = { value: string | null; source: string; confidence: number | null; flags: string[] };

export type Card = {
  id: string;
  batch_id: string;
  page: number;
  position: number;
  brand: string | null;
  battery_type: string | null;
  serial: string | null;
  batch_code: string | null;
  serial_full?: string;
  review_status?: string;
  part_no: string | null;
  date_of_sale: string | null;
  date_raw: string | null;
  vehicle_no: string | null;
  make_model: string | null;
  tel: string | null;
  bill_no: string | null;
  dealer_text: string | null;
  customer_name: string | null;
  signature_present: boolean | null;
  stamp_code: string | null;
  stamp_branch: number | null;
  stamp_tel: string | null;
  stamp_condition: string | null;
  dealer_stamp: string | null;
  qr_verified: boolean;
  status: string;
  excluded: boolean;
  duplicate_of_card_id: string | null;
  duplicate_kind: string | null;
  remarks: string | null;
  flags: string[];
  field_flags: Record<string, string[]>;
  needs_attention: boolean;
  sources: Record<string, Source[]>;
  duplicate_of?: {
    id: string;
    batch_id: string;
    file_name: string;
    uploaded_at: string | null;
    tel: string | null;
    vehicle_no: string | null;
    serial: string | null;
  };
};

const tokenKey = "warranty_token";

export function getToken() {
  return localStorage.getItem(tokenKey) || "";
}

export function setToken(token: string) {
  if (token) localStorage.setItem(tokenKey, token);
  else localStorage.removeItem(tokenKey);
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep status text */
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  const type = response.headers.get("content-type") || "";
  if (type.includes("application/json")) return response.json();
  return response as unknown as T;
}

export const api = {
  login: (email: string, password: string) =>
    request<{ access_token: string; user: User }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  me: () => request<User>("/api/auth/me"),
  dashboard: () => request<Record<string, unknown>>("/api/dashboard"),
  batches: () => request<Batch[]>("/api/batches"),
  batch: (id: string) => request<Batch>(`/api/batches/${id}`),
  upload: (files: File[]) => {
    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    return request<{ batches: Batch[] }>("/api/batches/upload", { method: "POST", body });
  },
  cards: (batchId: string) => request<Card[]>(`/api/batches/${batchId}/cards`),
  saveCard: (id: string, patch: Partial<Card>) =>
    request<Card>(`/api/cards/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  approveCard: (id: string) => request<Card>(`/api/cards/${id}/approve`, { method: "POST" }),
  approveBatch: (id: string) => request<Batch>(`/api/batches/${id}/approve`, { method: "POST" }),
  keepCard: (id: string) => request<Card>(`/api/cards/${id}/keep`, { method: "POST" }),
  search: (params: URLSearchParams) => request<{ total: number; cards: Card[] }>(`/api/search?${params}`),
  parts: (q = "") => request<PartRow[]>(`/api/part-numbers?q=${encodeURIComponent(q)}`),
  unmatched: () => request<{ battery_type: string; count: number }[]>("/api/part-numbers/unmatched"),
  savePart: (body: { battery_type: string; part_no: string }, id?: string) =>
    request(`/api/part-numbers${id ? `/${id}` : ""}`, {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(body),
    }),
  deletePart: (id: string) => request(`/api/part-numbers/${id}`, { method: "DELETE" }),
  assignPart: (body: { battery_type: string; part_no: string }) =>
    request("/api/part-numbers/assign", { method: "POST", body: JSON.stringify(body) }),
  partHistory: () => request<Record<string, string>[]>("/api/part-numbers/history"),
  importParts: (file: File, mode: string) => {
    const body = new FormData();
    body.append("file", file);
    return request<Record<string, unknown>>(`/api/part-numbers/import?mode=${mode}`, { method: "POST", body });
  },
  duplicates: () => request<Card[]>("/api/duplicates"),
  settings: () => request<{ config: Record<string, unknown>; zones: Record<string, number[]> }>("/api/settings"),
  saveSettings: (config: Record<string, unknown>) => request("/api/settings", { method: "PUT", body: JSON.stringify(config) }),
  saveZones: (zones: Record<string, number[]>) =>
    request("/api/settings/zones", { method: "PUT", body: JSON.stringify({ zones }) }),
  users: () => request<User[]>("/api/users"),
  audit: () => request<Record<string, unknown>[]>("/api/audit"),
  imageBlob: async (cardId: string) => {
    const response = await fetch(`/api/cards/${cardId}/image`, { headers: { Authorization: `Bearer ${getToken()}` } });
    if (!response.ok) throw new Error("Image not available");
    return URL.createObjectURL(await response.blob());
  },
};

export type PartRow = { id: string; battery_type: string; normalized_type: string; part_no: string; active: boolean };

export async function download(path: string, filename: string) {
  const response = await fetch(path, { headers: { Authorization: `Bearer ${getToken()}` } });
  if (!response.ok) {
    let detail = "Download failed";
    try {
      detail = (await response.json()).detail;
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : "Download failed");
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function fullSerial(card: Pick<Card, "serial" | "batch_code"> & { serial_full?: string }): string {
  if (card.serial_full) return card.serial_full;
  const serial = (card.serial || "").replace(/\s+/g, "").toUpperCase();
  const batch = (card.batch_code || "").replace(/\s+/g, "").toUpperCase();
  if (!serial) return "";
  if (!batch || batch === "0") return serial;
  return `${serial}${batch}`;
}

export function fieldTone(card: Card, field: string): string {
  const marks = card.field_flags?.[field] || [];
  if (marks.includes("claim")) return "claim";
  if (marks.includes("duplicate")) return "duplicate";
  if (marks.includes("disagree")) return "disagree";
  if (marks.includes("invalid") || marks.includes("unclear") || marks.includes("manual")) return "unclear";
  if (marks.includes("missing") || marks.includes("unmatched_part") || marks.includes("stamp_mismatch") || marks.includes("brand_mismatch"))
    return "missing";
  if (marks.includes("qr")) return "exact";
  return "";
}
