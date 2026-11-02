import { FormEvent, useEffect, useMemo, useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { createColumnHelper, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import { Batch, Card, PartRow, User, api, download, fieldTone, fullSerial, getToken, setToken } from "./api";

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      setReady(true);
      return;
    }
    api.me().then(setUser).catch(() => setToken("")).finally(() => setReady(true));
  }, []);

  if (!ready) return <p className="main">Loading…</p>;
  if (!user) {
    return (
      <Routes>
        <Route path="*" element={<Login onUser={setUser} />} />
      </Routes>
    );
  }

  return (
    <div className="shell">
      <nav className="nav">
        <strong>Warranty cards</strong>
        <NavLink to="/" end>Dashboard</NavLink>
        <NavLink to="/upload">Upload</NavLink>
        <NavLink to="/batches">Batches</NavLink>
        <NavLink to="/search">Search</NavLink>
        <NavLink to="/lookup">Part numbers</NavLink>
        <NavLink to="/duplicates">Duplicates</NavLink>
        {user.role === "admin" && <NavLink to="/settings">Settings</NavLink>}
        {user.role === "admin" && <NavLink to="/users">Users</NavLink>}
        {user.role === "admin" && <NavLink to="/audit">Audit</NavLink>}
        <div className="spacer" />
        <span style={{ padding: "8px 10px", fontSize: 13 }}>{user.name}<br />{user.role}</span>
        <button className="linkish" onClick={() => { setToken(""); setUser(null); }}>Sign out</button>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/upload" element={<Upload />} />
          <Route path="/batches" element={<Batches />} />
          <Route path="/batches/:id" element={<Verify />} />
          <Route path="/search" element={<Search />} />
          <Route path="/lookup" element={<Lookup role={user.role} />} />
          <Route path="/duplicates" element={<Duplicates />} />
          <Route path="/settings" element={user.role === "admin" ? <Settings /> : <Navigate to="/" />} />
          <Route path="/users" element={user.role === "admin" ? <Users /> : <Navigate to="/" />} />
          <Route path="/audit" element={user.role === "admin" ? <Audit /> : <Navigate to="/" />} />
        </Routes>
      </main>
    </div>
  );
}

function Login({ onUser }: { onUser: (user: User) => void }) {
  const [email, setEmail] = useState("operator@ekk.local");
  const [password, setPassword] = useState("Oper@12345");
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const result = await api.login(email, password);
      setToken(result.access_token);
      onUser(result.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    }
  }
  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <div className="eyebrow">E.K. Kanoo</div>
        <h1>Warranty Card Scanner</h1>
        <p>Read the printed label for free, then check the handwriting before anything is exported.</p>
        <label className="field">Email<input value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label className="field">Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        {error && <p className="error">{error}</p>}
        <button className="btn" type="submit">Sign in</button>
        <p className="hint">Demo: operator@ekk.local / Oper@12345, supervisor@ekk.local / Super@12345, admin@ekk.local / Admin@12345</p>
      </form>
    </div>
  );
}

function Dashboard() {
  const [data, setData] = useState<Record<string, any> | null>(null);
  useEffect(() => { api.dashboard().then(setData); }, []);
  if (!data) return <p>Loading dashboard…</p>;
  const usage = (data.usage || []) as Record<string, any>[];
  return (
    <>
      <h1>Dashboard</h1>
      <div className="grid-4">
        <Stat label="Batches" value={data.batches} />
        <Stat label="Cards kept" value={data.cards} />
        <Stat label="Needs review" value={data.pending_review} />
        <Stat label="Duplicates" value={data.duplicates} />
      </div>
      <div className="panel stat" style={{ marginTop: 12 }}>
        <span>AI cost this month</span>
        <b>${Number(data.ai_cost_month || 0).toFixed(4)}</b>
      </div>
      <h2>Usage by day and provider</h2>
      <SimpleTable
        columns={["day", "provider", "input_tokens", "output_tokens", "cost"]}
        rows={usage}
      />
    </>
  );
}

function Stat({ label, value }: { label: string; value: unknown }) {
  return <div className="panel stat"><span>{label}</span><b>{String(value ?? 0)}</b></div>;
}

function Upload() {
  const navigate = useNavigate();
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  async function send(files: File[]) {
    const pdfs = files.filter((file) => file.name.toLowerCase().endsWith(".pdf"));
    if (!pdfs.length) {
      setMessage("Choose one or more PDF files.");
      return;
    }
    setBusy(true);
    try {
      const result = await api.upload(pdfs);
      navigate(`/batches/${result.batches[0].id}`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <h1>Upload scans</h1>
      <div
        className="drop"
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          send(Array.from(event.dataTransfer.files));
        }}
      >
        <p>Drop warranty-card PDFs here.</p>
        <input type="file" accept="application/pdf" multiple onChange={(event) => send(Array.from(event.target.files || []))} />
        {busy && <p>Uploading…</p>}
        {message && <p className="error">{message}</p>}
      </div>
    </>
  );
}

function Batches() {
  const [rows, setRows] = useState<Batch[]>([]);
  const load = () => api.batches().then(setRows);
  useEffect(() => {
    load();
    const timer = window.setInterval(load, 2000);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <>
      <h1>Batches</h1>
      <BatchTable rows={rows} />
    </>
  );
}

function BatchTable({ rows }: { rows: Batch[] }) {
  const navigate = useNavigate();
  const helper = createColumnHelper<Batch>();
  const columns = useMemo(() => [
    helper.accessor("file_name", { header: "File" }),
    helper.accessor("status", { header: "Status" }),
    helper.accessor("card_count", { header: "Cards" }),
    helper.accessor("stamp_code_from_name", { header: "Stamp in name" }),
    helper.accessor((row) => row.progress?.message || "", { id: "progress", header: "Progress" }),
    helper.accessor("ai_cost", { header: "AI cost", cell: (info) => `$${Number(info.getValue() || 0).toFixed(4)}` }),
  ], []);
  const table = useReactTable({ data: rows, columns, getCoreRowModel: getCoreRowModel() });
  return (
    <table className="data">
      <thead>
        {table.getHeaderGroups().map((group) => (
          <tr key={group.id}>{group.headers.map((header) => <th key={header.id}>{flexRender(header.column.columnDef.header, header.getContext())}</th>)}</tr>
        ))}
      </thead>
      <tbody>
        {table.getRowModel().rows.map((row) => (
          <tr key={row.id} onClick={() => navigate(`/batches/${row.original.id}`)}>
            {row.getVisibleCells().map((cell) => <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function reviewLabel(card: Card): string {
  if (card.needs_attention || card.review_status === "pending") return "Pending";
  if (card.status === "approved" || card.review_status === "approved") return "Approved";
  return "OK";
}

const EDIT_FIELDS: { key: keyof Card; label: string }[] = [
  { key: "part_no", label: "Part No" },
  { key: "battery_type", label: "Battery type" },
  { key: "serial", label: "Serial no." },
  { key: "date_raw", label: "Date of sale" },
  { key: "vehicle_no", label: "Vehicle no." },
  { key: "make_model", label: "Make / model" },
  { key: "tel", label: "Telephone" },
  { key: "bill_no", label: "Bill no." },
  { key: "dealer_text", label: "Dealer" },
  { key: "customer_name", label: "Customer" },
  { key: "stamp_code", label: "Stamp code" },
  { key: "stamp_branch", label: "Branch no." },
  { key: "dealer_stamp", label: "Dealer stamp" },
  { key: "remarks", label: "Remarks" },
];

function Verify() {
  const { id = "" } = useParams();
  const [batch, setBatch] = useState<Batch | null>(null);
  const [cards, setCards] = useState<Card[]>([]);
  const [index, setIndex] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [imageUrl, setImageUrl] = useState("");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reviewId, setReviewId] = useState<string | null>(null);
  const card = cards.find((item) => item.id === reviewId) || null;

  async function load() {
    const [nextBatch, nextCards] = await Promise.all([api.batch(id), api.cards(id)]);
    setBatch(nextBatch);
    setCards(nextCards);
    return nextCards;
  }
  useEffect(() => { load().catch((err) => setError(String(err))); }, [id]);
  useEffect(() => {
    if (batch && ["uploaded", "scanning"].includes(batch.status)) {
      const timer = window.setInterval(load, 1500);
      return () => window.clearInterval(timer);
    }
  }, [batch?.status, id]);
  useEffect(() => {
    if (!card) return;
    const values: Record<string, string> = {};
    EDIT_FIELDS.forEach((field) => {
      if (field.key === "serial") {
        values.serial = fullSerial(card);
        return;
      }
      const raw = card[field.key];
      values[field.key] = raw == null ? "" : String(raw);
    });
    setDraft(values);
    setNotice("");
    let cancelled = false;
    api.imageBlob(card.id).then((url) => { if (!cancelled) setImageUrl((prev) => { if (prev) URL.revokeObjectURL(prev); return url; }); });
    return () => { cancelled = true; };
  }, [card?.id]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement;
      if (target && ["INPUT", "TEXTAREA"].includes(target.tagName)) return;
      if (event.key === "j" || event.key === "ArrowDown") setIndex((value) => Math.min(cards.length - 1, value + 1));
      if (event.key === "k" || event.key === "ArrowUp") setIndex((value) => Math.max(0, value - 1));
      if (event.key === "n") {
        const next = cards.findIndex((item, itemIndex) => itemIndex > index && item.needs_attention && !item.excluded);
        if (next >= 0) {
          setIndex(next);
          setReviewId(cards[next].id);
        }
      }
      if (event.key === "a" && card) approveOne();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cards, index, card, draft]);

  function valuesFrom(source: Card): Record<string, string> {
    const values: Record<string, string> = {};
    EDIT_FIELDS.forEach((field) => {
      if (field.key === "serial") {
        values.serial = fullSerial(source);
        return;
      }
      const raw = source[field.key];
      values[field.key] = raw == null ? "" : String(raw);
    });
    return values;
  }
  async function save() {
    if (!card) return null;
    setError("");
    setNotice("");
    const patch: Record<string, unknown> = {};
    const current = valuesFrom(card);
    EDIT_FIELDS.forEach((field) => {
      const next = draft[field.key] ?? "";
      if (next === (current[field.key] ?? "")) return;
      patch[field.key] = next;
    });
    if (patch.stamp_branch === "") patch.stamp_branch = null;
    else if (typeof patch.stamp_branch === "string") patch.stamp_branch = Number(patch.stamp_branch);
    if (Object.keys(patch).length === 0) {
      setNotice("Saved");
      return card;
    }
    try {
      const saved = await api.saveCard(card.id, patch as Partial<Card>);
      setCards((rows) => rows.map((row) => (row.id === saved.id ? saved : row)));
      setDraft(valuesFrom(saved));
      setNotice(saved.part_no ? `Saved. Part ${saved.part_no}` : "Saved. Part number is still empty because this battery type is not in the part list.");
      return saved;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
      return null;
    }
  }
  async function addPart() {
    if (!card) return;
    const batteryType = (draft.battery_type || "").trim();
    const partNo = (draft.part_no || "").trim();
    if (!batteryType || !partNo) {
      setNotice("");
      setError("Enter the battery type and the part number before adding it to the list.");
      return;
    }
    const stored = await save();
    if (!stored) return;
    try {
      await api.savePart({ battery_type: batteryType, part_no: partNo });
      const rows = await load();
      const fresh = rows.find((row) => row.id === card.id);
      if (fresh) setDraft(valuesFrom(fresh));
      setNotice(`Added ${partNo} for ${batteryType} to the part list.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the part number");
    }
  }
  async function approveOne() {
    if (!card) return;
    const stored = await save();
    if (!stored) return;
    try {
      const saved = await api.approveCard(card.id);
      setCards((rows) => rows.map((row) => (row.id === saved.id ? { ...row, ...saved } : row)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Approve failed");
    }
  }
  async function approveAll() {
    setError("");
    try {
      await api.approveBatch(id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Batch approval failed");
    }
  }

  const helper = createColumnHelper<Card>();
  const columns = useMemo(() => [
    helper.accessor((row) => `${row.page}.${row.position}`, { id: "pos", header: "#" }),
    helper.accessor("brand", { header: "Brand" }),
    helper.accessor((row) => fullSerial(row), { id: "serial", header: "Serial no." }),
    helper.accessor("part_no", { header: "Part" }),
    helper.accessor((row) => reviewLabel(row), { id: "status", header: "Status" }),
    helper.accessor((row) => (row.needs_attention ? "Review" : ""), { id: "attention", header: "Attention" }),
  ], []);
  const table = useReactTable({ data: cards, columns, getCoreRowModel: getCoreRowModel() });

  return (
    <>
      <div className="row">
        <h1 style={{ flex: 1 }}>{batch?.file_name || "Batch"}</h1>
        <span className="badge">{batch?.status}</span>
        <span>{batch?.progress?.message}</span>
      </div>
      {batch?.error && <p className="error">{batch.error}</p>}
      <div className="row">
        <button className="btn secondary" type="button" onClick={approveAll}>Approve batch</button>
        <button className="btn secondary" type="button" onClick={() => download(`/api/batches/${id}/export/data-loader`, `Data_Loader_${batch?.file_name || "cards"}.xlsx`).catch((err) => setError(String(err)))}>Data Loader</button>
        <button className="btn secondary" type="button" onClick={() => download(`/api/batches/${id}/export/detailed`, `Warranty_Cards_${batch?.file_name || "cards"}.xlsx`).catch((err) => setError(String(err)))}>Detailed Excel</button>
        <span className="hint">{cards.filter((item) => item.needs_attention && !item.excluded).length} cards need a check</span>
      </div>
      <div className="legend-row">
        <span className="swatch" style={{ background: "var(--red-bg)" }}>Red 0 = missing</span>
        <span className="swatch" style={{ background: "var(--yellow)" }}>Yellow = unclear / type it</span>
        <span className="swatch" style={{ background: "var(--orange)" }}>Orange = models disagree</span>
        <span className="swatch" style={{ background: "var(--purple)" }}>Purple = repeated serial</span>
        <span className="swatch" style={{ background: "var(--exact)" }}>Blue = read from the QR</span>
        <span className="hint">Keys: j/k next, n next problem, a approve</span>
      </div>
      <table className="data">
        <thead>{table.getHeaderGroups().map((group) => <tr key={group.id}>{group.headers.map((header) => <th key={header.id}>{String(header.column.columnDef.header)}</th>)}</tr>)}</thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr
              key={row.id}
              className={row.original.needs_attention ? "attention" : ""}
              onClick={() => {
                if (!row.original.needs_attention) return;
                setIndex(row.index);
                setReviewId(row.original.id);
              }}
            >
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id}>
                  {cell.column.id === "attention" && row.original.needs_attention ? (
                    <button className="review-link" type="button" onClick={() => { setIndex(row.index); setReviewId(row.original.id); }}>Review</button>
                  ) : (
                    flexRender(cell.column.columnDef.cell, cell.getContext())
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {card && (
        <div className="modal-back" onClick={() => setReviewId(null)}>
        <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="verify">
          <div className="viewer">
            {imageUrl && <img alt="Warranty card" src={imageUrl} style={{ transform: `scale(${zoom})` }} />}
            <div className="row" style={{ position: "absolute", left: 8, bottom: 8 }}>
              <button className="btn secondary" onClick={() => setZoom((value) => Math.max(0.4, value - 0.2))}>−</button>
              <button className="btn secondary" onClick={() => setZoom((value) => Math.min(3, value + 0.2))}>+</button>
            </div>
          </div>
          <form className="panel fields" onSubmit={(event) => { event.preventDefault(); save(); }}>
            <div className="row">
              <b>Card {card.page}.{card.position}</b>
              <span className="badge">{reviewLabel(card)}</span>
              <span className="badge">{card.brand || "brand?"}</span>
              {card.duplicate_kind && <span className="badge">{card.duplicate_kind}</span>}
              <button className="btn secondary" type="button" onClick={() => setReviewId(null)}>Close</button>
            </div>
            {card.duplicate_of && (
              <p className={card.duplicate_kind === "claim" ? "error" : ""}>
                First seen in {card.duplicate_of.file_name} on {card.duplicate_of.uploaded_at?.slice(0, 10)}. Phone {card.duplicate_of.tel || "—"}. Vehicle {card.duplicate_of.vehicle_no || "—"}.{" "}
                <a href={`/batches/${card.duplicate_of.batch_id}`}>Open that batch</a>
              </p>
            )}
            {EDIT_FIELDS.map((field) => (
              <label key={field.key} className={`field tone-${fieldTone(card, field.key)}`}>
                <span className="row"><span>{field.label}</span><span className="hint">sources<span className="tip">{(card.sources[field.key] || []).map((source) => `${source.source}: ${source.value || "—"} (${source.confidence ?? "n/a"})`).join("\n") || "No reading yet"}</span></span></span>
                <input value={draft[field.key] || ""} onChange={(event) => setDraft((current) => ({ ...current, [field.key]: event.target.value }))} />
              </label>
            ))}
            <p className="hint">Signature: {card.signature_present ? "present" : "not seen"}. Stamp: {card.stamp_condition || "—"} {card.stamp_tel || ""}</p>
            {error && <p className="error">{error}</p>}
            {notice && <p className="hint">{notice}</p>}
            <div className="row">
              <button className="btn" type="submit">Save</button>
              <button className="btn secondary" type="button" onClick={addPart}>Add to part list</button>
              <button className="btn warn" type="button" onClick={approveOne}>Approve card</button>
              {card.duplicate_kind === "within_pdf" && <button className="btn secondary" type="button" onClick={async () => { const saved = await api.keepCard(card.id); setCards((rows) => rows.map((row) => row.id === saved.id ? saved : row)); load(); }}>Keep this one</button>}
              <button className="btn secondary" type="button" onClick={() => { const next = cards.findIndex((item, itemIndex) => itemIndex > index && item.needs_attention); if (next >= 0) { setIndex(next); setReviewId(cards[next].id); } }}>Next problem</button>
            </div>
          </form>
        </div>
        </div>
        </div>
      )}
    </>
  );
}

function Search() {
  const [q, setQ] = useState("");
  const [brand, setBrand] = useState("");
  const [rows, setRows] = useState<Card[]>([]);
  const [total, setTotal] = useState(0);
  const [duplicatesOnly, setDuplicatesOnly] = useState(false);
  async function run(event?: FormEvent) {
    event?.preventDefault();
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (brand) params.set("brand", brand);
    if (duplicatesOnly) params.set("duplicates_only", "true");
    const result = await api.search(params);
    setRows(result.cards);
    setTotal(result.total);
  }
  useEffect(() => { run(); }, []);
  const helper = createColumnHelper<Card>();
  const columns = useMemo(() => [
    helper.accessor((row) => fullSerial(row), { id: "serial", header: "Serial no." }),
    helper.accessor("part_no", { header: "Part No" }),
    helper.accessor("tel", { header: "Phone" }),
    helper.accessor("vehicle_no", { header: "Vehicle" }),
    helper.accessor("date_of_sale", { header: "Date" }),
    helper.accessor("brand", { header: "Brand" }),
    helper.accessor("stamp_code", { header: "Stamp" }),
  ], []);
  const table = useReactTable({ data: rows, columns, getCoreRowModel: getCoreRowModel() });
  const navigate = useNavigate();
  return (
    <>
      <h1>Search</h1>
      <form className="row" onSubmit={run}>
        <input placeholder="Serial, phone, part, vehicle, bill" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={brand} onChange={(e) => setBrand(e.target.value)}>
          <option value="">All brands</option>
          <option>CEIL</option>
          <option>SF SONIC</option>
          <option>DYNEX</option>
        </select>
        <label><input type="checkbox" checked={duplicatesOnly} onChange={(e) => setDuplicatesOnly(e.target.checked)} /> Duplicates only</label>
        <button className="btn" type="submit">Search</button>
        <button className="btn secondary" type="button" onClick={() => download(`/api/search/export?q=${encodeURIComponent(q)}&brand=${encodeURIComponent(brand)}&duplicates_only=${duplicatesOnly}`, "search.xlsx")}>Export</button>
        <span>{total} cards</span>
      </form>
      <table className="data">
        <thead>{table.getHeaderGroups().map((group) => <tr key={group.id}>{group.headers.map((header) => <th key={header.id}>{String(header.column.columnDef.header)}</th>)}</tr>)}</thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id} onClick={() => navigate(`/batches/${row.original.batch_id}`)}>
              {row.getVisibleCells().map((cell) => <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function normaliseType(value: string): string {
  return value.replace(/\s+/g, "").toUpperCase().replaceAll("FSEO", "FSE0").replaceAll("FDMO", "FDM0");
}

function Lookup({ role }: { role: string }) {
  const canEdit = role === "admin" || role === "supervisor";
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<PartRow[]>([]);
  const [unmatched, setUnmatched] = useState<{ battery_type: string; count: number }[]>([]);
  const [history, setHistory] = useState<Record<string, string>[]>([]);
  const [diff, setDiff] = useState<Record<string, any> | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [form, setForm] = useState({ battery_type: "", part_no: "" });
  const [partMessage, setPartMessage] = useState("");
  const [partError, setPartError] = useState("");
  const load = async () => {
    setRows(await api.parts(q));
    setUnmatched(await api.unmatched());
    if (canEdit) setHistory(await api.partHistory());
  };
  useEffect(() => { load(); }, []);
  const helper = createColumnHelper<PartRow>();
  const columns = useMemo(() => [
    helper.accessor("battery_type", { header: "Battery type" }),
    helper.accessor("part_no", { header: "BATTERY_ITEM_CODE" }),
    helper.accessor("normalized_type", { header: "Normalised" }),
  ], []);
  const table = useReactTable({ data: rows, columns, getCoreRowModel: getCoreRowModel() });
  return (
    <>
      <h1>Part numbers</h1>
      <form className="row" onSubmit={(event) => { event.preventDefault(); load(); }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search type or part" />
        <button className="btn" type="submit">Search</button>
        <button className="btn secondary" type="button" onClick={() => download("/api/part-numbers/export", "Battery_Part_No.xlsx")}>Export</button>
      </form>
      {partMessage && <p className="hint">{partMessage}</p>}
      {partError && <p className="error">{partError}</p>}
      <form onSubmit={async (event) => {
        event.preventDefault();
        setPartError("");
        setPartMessage("");
        try {
          await api.savePart(form);
          setPartMessage(`Added ${form.part_no.trim().toUpperCase()} for ${form.battery_type.trim()}.`);
          setForm({ battery_type: "", part_no: "" });
          await load();
        } catch (err) {
          setPartError(err instanceof Error ? err.message : "Could not add the part number");
        }
      }}>
      <table className="data part-table">
        <thead>{table.getHeaderGroups().map((group) => <tr key={group.id}>{group.headers.map((header) => <th key={header.id}>{String(header.column.columnDef.header)}</th>)}<th></th></tr>)}</thead>
        <tbody>
          <tr className="part-entry">
            <td><input aria-label="Battery type" placeholder="FCE7-DIN100L(EFB)" value={form.battery_type} onChange={(e) => setForm({ ...form, battery_type: e.target.value })} required /></td>
            <td><input aria-label="BATTERY_ITEM_CODE" placeholder="CEFBL4DIN100L" value={form.part_no} onChange={(e) => setForm({ ...form, part_no: e.target.value })} required /></td>
            <td className="normalised">{form.battery_type.trim() ? normaliseType(form.battery_type) : "—"}</td>
            <td><button className="btn" type="submit">Add</button></td>
          </tr>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id}>
              {row.getVisibleCells().map((cell) => <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}
              <td>{canEdit && <button className="btn secondary" type="button" onClick={async () => { await api.deletePart(row.original.id); load(); }}>Delete</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </form>
      <h2>Unmatched types from open batches</h2>
      {unmatched.map((item) => (
        <form key={item.battery_type} className="row" onSubmit={async (event) => {
          event.preventDefault();
          const part = new FormData(event.currentTarget).get("part");
          await api.assignPart({ battery_type: item.battery_type, part_no: String(part || "") });
          load();
        }}>
          <span>{item.battery_type} ({item.count})</span>
          <input name="part" placeholder="Assign part no" required />
          <button className="btn" type="submit">Assign</button>
        </form>
      ))}
      {canEdit && (
        <>
          <h2>Import Excel</h2>
          <input type="file" accept=".xlsx" onChange={(event) => setFile(event.target.files?.[0] || null)} />
          <div className="row">
            <button className="btn secondary" type="button" disabled={!file} onClick={async () => file && setDiff(await api.importParts(file, "preview"))}>Preview diff</button>
            <button className="btn" type="button" disabled={!file} onClick={async () => { if (file) { await api.importParts(file, "merge"); load(); } }}>Merge</button>
            <button className="btn secondary" type="button" disabled={!file} onClick={async () => { if (file) { await api.importParts(file, "replace"); load(); } }}>Replace</button>
          </div>
          {diff && <pre>{JSON.stringify({ added: diff.added?.length, changed: diff.changed?.length, removed: diff.removed?.length, unchanged: diff.unchanged?.length }, null, 2)}</pre>}
          <h2>History</h2>
          <SimpleTable columns={["created_at", "action", "battery_type", "old_part_no", "new_part_no"]} rows={history} />
        </>
      )}
    </>
  );
}

function Duplicates() {
  const [rows, setRows] = useState<Card[]>([]);
  useEffect(() => { api.duplicates().then(setRows); }, []);
  return (
    <>
      <div className="row"><h1 style={{ flex: 1 }}>Duplicates</h1><button className="btn secondary" onClick={() => download("/api/duplicates/export", "duplicates.xlsx")}>Export</button></div>
      <SimpleTable columns={["serial", "duplicate_kind", "tel", "vehicle_no", "batch_id"]} rows={rows as unknown as Record<string, unknown>[]} />
    </>
  );
}

function Settings() {
  const [config, setConfig] = useState<Record<string, any> | null>(null);
  const [zones, setZones] = useState<Record<string, number[]>>({});
  const [message, setMessage] = useState("");
  useEffect(() => { api.settings().then((data) => { setConfig(data.config); setZones(data.zones); }); }, []);
  if (!config) return <p>Loading settings…</p>;
  const providers = (config.providers || {}) as Record<string, any>;
  return (
    <>
      <h1>Settings</h1>
      <div className="panel" style={{ padding: 16 }}>
        <label className="field"><span>AI enabled</span>
          <select value={String(config.ai_enabled)} onChange={(e) => setConfig({ ...config, ai_enabled: e.target.value === "true" })}>
            <option value="true">On</option>
            <option value="false">Off — Stage 1 only, type the handwriting</option>
          </select>
        </label>
        <label className="field">Confidence threshold<input type="number" step="0.05" value={config.confidence_threshold} onChange={(e) => setConfig({ ...config, confidence_threshold: Number(e.target.value) })} /></label>
        <label className="field">Cards per AI request<input type="number" value={config.cards_per_request} onChange={(e) => setConfig({ ...config, cards_per_request: Number(e.target.value) })} /></label>
        <label className="field">Max spend per batch<input type="number" step="0.1" value={config.max_batch_spend} onChange={(e) => setConfig({ ...config, max_batch_spend: Number(e.target.value) })} /></label>
        <label className="field">Max spend per month<input type="number" step="1" value={config.max_month_spend} onChange={(e) => setConfig({ ...config, max_month_spend: Number(e.target.value) })} /></label>
        <label className="field">Date window (months)<input type="number" value={config.date_max_age_months} onChange={(e) => setConfig({ ...config, date_max_age_months: Number(e.target.value) })} /></label>
        <label className="field">Duplicate policy
          <select value={config.duplicate_policy} onChange={(e) => setConfig({ ...config, duplicate_policy: e.target.value })}>
            <option value="warn">Warn only</option>
            <option value="block">Block approval</option>
            <option value="supervisor">Supervisor must approve</option>
          </select>
        </label>
        <label className="field">Stage 2 provider order<input value={(config.stage2_order || []).join(", ")} onChange={(e) => setConfig({ ...config, stage2_order: e.target.value.split(",").map((item) => item.trim()).filter(Boolean) })} /></label>
        <label className="field">Stage 3 provider order<input value={(config.stage3_order || []).join(", ")} onChange={(e) => setConfig({ ...config, stage3_order: e.target.value.split(",").map((item) => item.trim()).filter(Boolean) })} /></label>
        {Object.entries(providers).map(([name, provider]) => (
          <fieldset key={name}>
            <legend>{name} {provider.api_key_set ? "(key saved)" : "(no key)"}</legend>
            <label className="field">Model<input value={provider.model} onChange={(e) => setConfig({ ...config, providers: { ...providers, [name]: { ...provider, model: e.target.value } } })} /></label>
            <label className="field">Base URL<input value={provider.base_url} onChange={(e) => setConfig({ ...config, providers: { ...providers, [name]: { ...provider, base_url: e.target.value } } })} /></label>
            <label className="field">API key (leave blank to keep)<input type="password" placeholder="stored encrypted" onChange={(e) => setConfig({ ...config, providers: { ...providers, [name]: { ...provider, api_key: e.target.value } } })} /></label>
            <div className="row">
              <label>Input / 1M <input type="number" step="0.01" value={provider.input_per_million} onChange={(e) => setConfig({ ...config, providers: { ...providers, [name]: { ...provider, input_per_million: Number(e.target.value) } } })} /></label>
              <label>Output / 1M <input type="number" step="0.01" value={provider.output_per_million} onChange={(e) => setConfig({ ...config, providers: { ...providers, [name]: { ...provider, output_per_million: Number(e.target.value) } } })} /></label>
            </div>
          </fieldset>
        ))}
        <button className="btn" onClick={async () => { await api.saveSettings(config); setMessage("Saved"); }}>Save settings</button>
        <h2>Data Loader template</h2>
        <input type="file" accept=".xlsx" onChange={async (event) => {
          const chosen = event.target.files?.[0];
          if (!chosen) return;
          const body = new FormData();
          body.append("file", chosen);
          const response = await fetch("/api/settings/template", { method: "POST", headers: { Authorization: `Bearer ${getToken()}` }, body });
          setMessage(response.ok ? "Template replaced" : "Template rejected");
        }} />
        <h2>Card zones</h2>
        <p className="hint">Fractions of an upright card: x0, y0, x1, y1. CEIL, SF SONIC and DYNEX share one layout.</p>
        {Object.entries(zones).map(([name, box]) => (
          <label key={name} className="field">{name}
            <input value={box.join(", ")} onChange={(event) => {
              const next = event.target.value.split(",").map((item) => Number(item.trim()));
              if (next.length === 4 && next.every((item) => !Number.isNaN(item))) setZones({ ...zones, [name]: next });
            }} />
          </label>
        ))}
        <button className="btn" onClick={async () => { await api.saveZones(zones); setMessage("Zones saved"); }}>Save zones</button>
        {message && <p>{message}</p>}
      </div>
    </>
  );
}

function Users() {
  const [rows, setRows] = useState<User[]>([]);
  const [form, setForm] = useState({ email: "", name: "", password: "ChangeMe123", role: "operator" });
  const load = () => api.users().then(setRows);
  useEffect(() => { load(); }, []);
  return (
    <>
      <h1>Users</h1>
      <form className="row" onSubmit={async (event) => { event.preventDefault(); await fetch("/api/users", { method: "POST", headers: { Authorization: `Bearer ${getToken()}`, "Content-Type": "application/json" }, body: JSON.stringify(form) }); load(); }}>
        <input placeholder="Email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        <input placeholder="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <input placeholder="Password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
          <option value="operator">operator</option>
          <option value="supervisor">supervisor</option>
          <option value="admin">admin</option>
        </select>
        <button className="btn" type="submit">Add user</button>
      </form>
      <SimpleTable columns={["email", "name", "role", "active"]} rows={rows as unknown as Record<string, unknown>[]} />
    </>
  );
}

function Audit() {
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  useEffect(() => { api.audit().then(setRows); }, []);
  return (
    <>
      <h1>Audit log</h1>
      <SimpleTable columns={["created_at", "action", "entity_type", "entity_id", "user_id"]} rows={rows} />
    </>
  );
}

function SimpleTable({ columns, rows }: { columns: string[]; rows: Record<string, unknown>[] }) {
  return (
    <table className="data">
      <thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
      <tbody>
        {rows.map((row, index) => (
          <tr key={index}>{columns.map((column) => <td key={column}>{String(row[column] ?? "")}</td>)}</tr>
        ))}
      </tbody>
    </table>
  );
}
