import { useState, useEffect, useRef } from "react";

// ── BRAND ────────────────────────────────────────────────────
const S = {
  bg:      "#101113",
  surface: "#17181B",
  card:    "#17181B",
  border:  "rgba(255,255,255,.08)",
  neon:    "#CAFF00",
  cobalt:  "#3D7EFF",
  warning: "#FFB800",
  danger:  "#FF5C5C",
  text:    "#F5F6F7",
  muted:   "#9CA0AA",
  dim:     "#6B7079",
};

const FONT_NUM = "'Barlow Condensed', sans-serif";

// ── HELPERS ──────────────────────────────────────────────────
const MESES = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"];

function fmtWeek(w) {
  const d = w.slice(0, 10);
  return MESES[parseInt(d.slice(5, 7)) - 1] + " " + parseInt(d.slice(8, 10));
}

function fmtDate(d) {
  if (!d) return "";
  return parseInt(d.slice(8, 10)) + " " + MESES[parseInt(d.slice(5, 7)) - 1];
}

function getUserId() {
  return new URLSearchParams(window.location.search).get("u") || "demo";
}

function getDataUrl(userId) {
  return `${import.meta.env.BASE_URL}data/${userId}.json`;
}


function daysUntil(dateStr, fromStr) {
  const target = new Date(dateStr);
  const from = fromStr ? new Date(fromStr) : new Date();
  return Math.ceil((target - from) / 86400000);
}

// ── SUBCOMPONENTS ─────────────────────────────────────────────
function SwetroLogo({ className }) {
  return (
    <img
      className={className}
      src={`${import.meta.env.BASE_URL}swetro-logo.png`}
      alt="Swetro"
    />
  );
}

// Ícono de info con popover que se abre/cierra con tap — funciona en móvil,
// a diferencia de title (que depende de hover) o CSS-only :hover tooltips.
function InfoTip({ text }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const [pos, setPos] = useState({ top: 0, left: 0 });

  useEffect(() => {
    if (!open) return;
    const closeIfOutside = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeIfOutside);
    document.addEventListener("keydown", (e) => e.key === "Escape" && setOpen(false));
    return () => document.removeEventListener("pointerdown", closeIfOutside);
  }, [open]);

  const toggle = (e) => {
    e.stopPropagation();
    if (!open && wrapRef.current) {
      const r = wrapRef.current.getBoundingClientRect();
      const width = Math.min(240, window.innerWidth - 24);
      let left = r.left + r.width / 2 - width / 2;
      left = Math.max(12, Math.min(left, window.innerWidth - width - 12));
      setPos({ top: r.bottom + 8, left, width });
    }
    setOpen(o => !o);
  };

  return (
    <span ref={wrapRef} style={{ position: "relative", display: "inline-flex", verticalAlign: "middle" }}>
      <button type="button" className="infotip-btn" aria-label="Más información" aria-expanded={open} onClick={toggle}>?</button>
      {open && (
        <div className="infotip-pop" style={{ top: pos.top, left: pos.left, width: pos.width || 240 }}>
          {text}
        </div>
      )}
    </span>
  );
}

function ACWRSlider({ acwr, color }) {
  const pct = Math.min(Math.max(((acwr - 0.5) / 1.5) * 100, 2), 97);
  return (
    <div style={{ position: "relative", height: 8, borderRadius: 4,
      background: "linear-gradient(90deg,#3D7EFF 0%,#CAFF00 40%,#FFB800 65%,#FF5C5C 100%)", marginTop: 12 }}>
      <div style={{ position: "absolute", left: `${pct}%`, top: -4, width: 16, height: 16,
        background: S.text, borderRadius: "50%", transform: "translateX(-50%)",
        border: `2px solid ${color}`, boxShadow: "0 1px 4px rgba(0,0,0,.4)" }} />
    </div>
  );
}

function TagBadge({ tag }) {
  const colors = {
    LARGO:      { bg: "#1a3a1a", color: S.neon,   border: "#2a5a2a" },
    INTERVALOS: { bg: "#1a1a3a", color: S.cobalt, border: "#2a2a5a" },
    TEMPO:      { bg: "#3a1a1a", color: S.danger, border: "#5a2a2a" },
    RODAJE:     { bg: "#222",    color: S.muted,  border: "#333" },
  };
  const c = colors[tag] || colors.RODAJE;
  return (
    <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".06em",
      background: c.bg, color: c.color, border: `1px solid ${c.border}`,
      borderRadius: 4, padding: "3px 7px", flexShrink: 0 }}>{tag}</span>
  );
}

function getTag(a) {
  if (a.dist_km >= 16) return "LARGO";
  if (/\d+\s*[xX]\s*\d/.test(a.name)) return "INTERVALOS";
  if (a.pace_raw < 6.0) return "TEMPO";
  return "RODAJE";
}

// Gráfica de tendencia con delta, área y rango
function TrendChart({ data, labels, color, unit = "", goodWhen = "up", decimals = 0 }) {
  if (!data || data.length < 2) return null;
  const last = data[data.length - 1];
  const prev = data[data.length - 2];
  const delta = last - prev;
  const neutral = Math.abs(delta) < 0.005;
  const good = goodWhen === "down" ? delta < 0 : delta > 0;
  const deltaColor = neutral ? S.muted : good ? S.neon : S.danger;
  const arrow = neutral ? "→" : delta > 0 ? "▲" : "▼";
  const fmt = v => decimals > 0 ? v.toFixed(decimals) : String(Math.round(v));

  const min = Math.min(...data), max = Math.max(...data), range = max - min || 1;
  const W = 100, H = 40;
  const pts = data.map((v, i) => {
    const x = 2 + (i / (data.length - 1)) * 96;
    const y = 4 + (1 - (v - min) / range) * 32;
    return x.toFixed(1) + "," + y.toFixed(1);
  });
  const line = pts.join(" ");
  const gid = "tg" + color.replace("#", "") + (unit || "x");
  const areaLine = pts[0].split(",")[0] + "," + H + " " + line + " " + pts[pts.length-1].split(",")[0] + "," + H;

  return (
    <div style={{ display: "flex", flexDirection: "column", flex: 1 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, marginBottom: 6 }}>
        <span style={{ color: deltaColor, fontWeight: 700 }}>
          {arrow} {fmt(Math.abs(delta))}{unit ? " " + unit : ""}
        </span>
        <span style={{ color: S.dim }}>vs. anterior</span>
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>
        <svg viewBox={"0 0 " + W + " " + H} preserveAspectRatio="none"
          style={{ width: "100%", height: "100%", display: "block" }}>
          <defs>
            <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity="0.25" />
              <stop offset="100%" stopColor={color} stopOpacity="0" />
            </linearGradient>
          </defs>
          <line x1="2" y1={H - 1} x2="98" y2={H - 1} stroke="#2A2D32" strokeWidth="0.5" />
          <polyline points={areaLine} fill={"url(#" + gid + ")"} stroke="none" />
          <polyline points={line} fill="none" stroke={color}
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            vectorEffect="non-scaling-stroke" />
        </svg>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, color: S.dim, marginTop: 4 }}>
        <span>{labels[0]}</span>
        <span>{fmt(min)} – {fmt(max)}</span>
        <span>{labels[labels.length - 1]}</span>
      </div>
    </div>
  );
}


function LoadingScreen() {
  return (
    <div style={{ minHeight: "100vh", background: S.bg, display: "flex",
      flexDirection: "column", alignItems: "center", justifyContent: "center",
      fontFamily: "Poppins, sans-serif", gap: 24 }}>
      <SwetroLogo className="side-logo" />
      <div style={{ width: 36, height: 36, borderRadius: "50%",
        border: "3px solid #2A2D32", borderTop: `3px solid ${S.neon}`,
        animation: "spin 1s linear infinite" }} />
      <style>{`@keyframes spin { to { transform: rotate(360deg) } }`}</style>
    </div>
  );
}

function ErrorScreen({ msg }) {
  return (
    <div style={{ minHeight: "100vh", background: S.bg, display: "flex",
      flexDirection: "column", alignItems: "center", justifyContent: "center",
      fontFamily: "Poppins, sans-serif", gap: 12 }}>
      <SwetroLogo className="side-logo" />
      <div style={{ fontSize: 16, color: S.danger, marginTop: 12 }}>No se pudo cargar el perfil</div>
      <div style={{ fontSize: 14, color: S.muted }}>{msg}</div>
    </div>
  );
}

// ── TABS (Retos oculto por ahora) ────────────────────────────
const TABS = ["PULSE", "SEMANA", "CARGA"];

// ── MAIN ─────────────────────────────────────────────────────
export default function App() {
  const [data, setData]   = useState(null);
  const [error, setError] = useState(null);
  const [tab, setTab]     = useState("PULSE");
  const [pulseExp, setPulseExp] = useState(false);

  useEffect(() => {
    fetch(getDataUrl(getUserId()))
      .then(r => { if (!r.ok) throw new Error(`Usuario no encontrado (${r.status})`); return r.json(); })
      .then(d => setData(d))
      .catch(e => setError(e.message));
  }, []);

  if (error) return <ErrorScreen msg={error} />;
  if (!data) return <LoadingScreen />;

  const { meta, activities, weekly, acwr: acwrData, pulse, taper } = data;
  const primerNombre = (meta.nombre || "").split(" ")[0];
  const nombreDisplay = primerNombre
    ? primerNombre.charAt(0) + primerNombre.slice(1).toLowerCase()
    : "";
  const hasMeta = meta.metaCarrera.fecha !== "2027-01-01";
  const daysLeft = daysUntil(meta.metaCarrera.fecha, meta.generadoEn);
  const FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLScpOyf-mD9AjSIlLm1zGIiZQN8E7Dj1yv6tu0-Pj7EMBGL2ow/viewform";
  const lastWeek = weekly[weekly.length - 1];
  const prevWeek = weekly[weekly.length - 2];
  const acwr = acwrData?.valor ?? null;
  const acwrConfiable = !!acwrData?.confiable;
  const weeklyIncrease = prevWeek && prevWeek.total_km > 0
    ? Math.round(((lastWeek.total_km - prevWeek.total_km) / prevWeek.total_km) * 100)
    : 0;
  const incSign = (weeklyIncrease >= 0 ? "+" : "") + weeklyIncrease + "%";
  const acwrColor = acwr === null || !acwrConfiable
    ? S.dim
    : acwr > 1.5 ? S.danger : acwr > 1.3 ? S.warning : acwr < 0.8 ? S.cobalt : S.neon;
  const acwrLabel = acwr === null
    ? "SIN DATOS SUFICIENTES"
    : !acwrConfiable
      ? "BASE AÚN LIMITADA"
      : acwr > 1.5 ? "ZONA DE RIESGO" : acwr > 1.3 ? "ZONA DE ALERTA" : acwr < 0.8 ? "SUBENTRENAMIENTO" : "ZONA SEGURA";
  const acwrDisplay = acwr === null ? "—" : `${acwr}x`;

  const last8 = weekly.slice(-8);
  const maxKm = Math.max(...last8.map(w => w.total_km), 1);
  const weekLabels = last8.map(w => fmtWeek(w.week));

  const recentSessions = [...activities].reverse().slice(0, 6).map(a => ({ ...a, tag: getTag(a) }));

  const nextSession = pulse?.weekPlan?.[0] || null;
  const restWeekPlan = pulse?.weekPlan?.slice(1) || [];

  const balanceMetric = pulse?.keyMetrics?.find(m => (m.label || "").toLowerCase().includes("fácil"));

  const css = `
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&family=Barlow+Condensed:wght@700;800;900&display=swap');
    @keyframes spin { to { transform: rotate(360deg) } }
    * { box-sizing: border-box; margin: 0; padding: 0 }
    html, body, #root { width: 100%; background: ${S.bg}; text-align: left }
    body { font-family: 'Poppins', sans-serif; color: ${S.text}; font-size: 16px }
    ::-webkit-scrollbar { width: 3px; height: 3px }
    ::-webkit-scrollbar-thumb { background: #2A2D32; border-radius: 2px }
    button { font-family: 'Poppins', sans-serif; cursor: pointer }

    .infotip-btn { width: 20px; height: 20px; border-radius: 50%; flex-shrink: 0;
      background: rgba(255,255,255,.06); border: 1px solid ${S.border}; color: ${S.muted};
      font-size: 12px; font-weight: 700; line-height: 1; display: inline-flex;
      align-items: center; justify-content: center; padding: 0; margin-left: 2px }
    .infotip-btn:hover, .infotip-btn[aria-expanded="true"] { color: ${S.text}; border-color: ${S.muted} }
    .infotip-pop { position: fixed; z-index: 100; background: #1E2024; border: 1px solid ${S.border};
      border-radius: 12px; padding: 12px 14px; font-size: 13px; line-height: 1.5; color: ${S.muted};
      box-shadow: 0 8px 24px rgba(0,0,0,.5); text-align: left }

    .app { display: flex; min-height: 100vh; background: ${S.bg} }

    .sidebar { width: 240px; background: ${S.bg}; border-right: 1px solid ${S.border};
      display: flex; flex-direction: column; position: fixed; top: 0; bottom: 0; left: 0;
      z-index: 10; padding: 24px 14px; gap: 4px }
    .side-logo { height: 24px; width: auto; display: block; margin: 0 10px 24px }
    .nav-btn { display: flex; align-items: center; gap: 10px; padding: 10px 12px;
      background: transparent; border: 1px solid transparent; border-radius: 10px;
      color: ${S.muted}; font-size: 14px; font-weight: 600; letter-spacing: .04em;
      text-align: left; transition: all .15s; white-space: nowrap }
    .nav-btn.active { background: rgba(202,255,0,.08); border-color: rgba(202,255,0,.25);
      color: ${S.neon}; font-weight: 700 }
    .nav-dot { width: 6px; height: 6px; border-radius: 50%; background: #2A2D32; flex-shrink: 0 }
    .nav-btn.active .nav-dot { background: ${S.neon} }
    .countdown-box { margin-top: auto; background: ${S.card}; border: 1px solid ${S.border};
      border-radius: 16px; padding: 16px 18px }

    .main { margin-left: 240px; flex: 1; display: flex; flex-direction: column; min-width: 0 }
    .topbar { border-bottom: 1px solid ${S.border}; padding: 14px 32px; font-size: 15px;
      color: ${S.muted}; position: sticky; top: 0; background: ${S.bg}; z-index: 5 }
    .content { padding: 28px 32px }
    .page-title { font-family: ${FONT_NUM}; font-size: 36px; font-weight: 900;
      color: ${S.text}; margin-bottom: 22px }
    .card { background: ${S.card}; border: 1px solid ${S.border}; border-radius: 20px;
      padding: 22px 24px }
    .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px }
    .grid3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 16px }

    .session-date { width: 56px; flex-shrink: 0; font-size: 14px; color: ${S.muted};
      white-space: nowrap; font-weight: 500 }

    @media (max-width: 820px) {
      .sidebar { flex-direction: row; align-items: center; width: 100%; height: auto;
        bottom: auto; padding: 10px 12px; gap: 4px; border-right: none;
        border-bottom: 1px solid ${S.border}; overflow-x: auto }
      .side-logo { height: 18px; margin: 0 10px 0 0 }
      .countdown-box { display: none }
      .nav-btn { padding: 8px 10px; font-size: 13px }
      .main { margin-left: 0; margin-top: 54px }
      .topbar { padding: 12px 16px }
      .content { padding: 16px 14px }
      .grid2, .grid3 { grid-template-columns: 1fr } .card { min-height: auto !important }
      .page-title { font-size: 30px; margin-bottom: 16px }
      .card { padding: 18px 16px; border-radius: 16px }
      .bar-lbl { font-size: 13px !important }
      .bar-val { font-size: 13px !important }
    }
  `;

  return (
    <div className="app">
      <style>{css}</style>

      {/* ── SIDEBAR ── */}
      <div className="sidebar">
        <SwetroLogo className="side-logo" />
        {TABS.map(t => (
          <button key={t} className={`nav-btn${tab === t ? " active" : ""}`} onClick={() => setTab(t)}>
            <div className="nav-dot" /> {t}
          </button>
        ))}
        <div className="countdown-box">
          {hasMeta && (
            <div style={{ fontFamily: FONT_NUM, fontSize: 36, fontWeight: 900, color: S.text, lineHeight: 1 }}>
              {daysLeft}<span style={{ fontSize: 14, color: S.muted, fontFamily: "Poppins", marginLeft: 4 }}>D</span>
            </div>
          )}
          <div style={{ fontSize: 14, color: S.muted, marginTop: hasMeta ? 4 : 0 }}>
            {hasMeta
              ? <>para tu <strong style={{ color: S.text }}>{meta.metaCarrera.nombre}</strong></>
              : <a href={FORM_URL} target="_blank" rel="noopener noreferrer"
                  style={{ color: S.neon, fontWeight: 700, textDecoration: "none", fontSize: 14, lineHeight: 1.5 }}>
                  ¿Cuál es tu próxima carrera? →
                </a>
            }
          </div>
        </div>
      </div>

      {/* ── MAIN ── */}
      <div className="main">
        <div className="topbar">
          {nombreDisplay && <strong style={{ color: S.text }}>{nombreDisplay}</strong>}
          {nombreDisplay && " · "}
          {hasMeta
            ? <>{daysLeft} días para tu <strong style={{ color: S.text }}>{meta.metaCarrera.nombre}</strong></>
            : <a href={FORM_URL} target="_blank" rel="noopener noreferrer"
                style={{ color: S.neon, fontWeight: 700, textDecoration: "none" }}>
                Registra tu meta de entrenamiento →
              </a>
          }
        </div>

        <div className="content">

          {/* ══ PULSE ══ */}
          {tab === "PULSE" && pulse && (
            <div>
              <h1 className="page-title">Análisis PULSE</h1>

              <div className="grid2">
                {/* Score */}
                <div className="card" style={{ background: "linear-gradient(160deg,rgba(202,255,0,.09),rgba(61,126,255,.07) 70%)", borderColor: "rgba(202,255,0,.22)" }}>
                  <div style={{ fontSize: 13, color: S.neon, fontWeight: 700, letterSpacing: ".08em", marginBottom: 12, display: "flex", alignItems: "center" }}>
                    TU PULSE DE HOY
                    <InfoTip text="Puntaje 0-100 que resume tu semana de entrenamiento: volumen, intensidad y recuperación, generado por IA. Más alto no siempre es mejor — una semana de descanso planeado también puede dar un puntaje bajo." />
                  </div>
                  <div style={{ fontFamily: FONT_NUM, fontSize: 72, fontWeight: 900, color: S.text, lineHeight: 1 }}>
                    {pulse.score}<span style={{ fontSize: 22, color: S.dim, fontFamily: "Poppins", fontWeight: 600 }}>/100</span>
                  </div>
                  <div style={{ fontSize: 16, fontWeight: 600, color: S.text, marginTop: 12, lineHeight: 1.4 }}>
                    {pulse.headline}
                  </div>
                  {(() => {
                    const s = pulse.score;
                    const ranges = [
                      { max: 30, label: "Necesita atención urgente", color: S.danger },
                      { max: 50, label: "Por debajo de lo esperado", color: S.warning },
                      { max: 70, label: "En camino, puede mejorar", color: S.warning },
                      { max: 85, label: "Buena semana de entrenamiento", color: S.neon },
                      { max: 100, label: "Semana excelente", color: S.neon },
                    ];
                    const r = ranges.find(r => s <= r.max) || ranges[ranges.length - 1];
                    return (
                      <div style={{ marginTop: 14 }}>
                        <div style={{ display: "flex", gap: 2, marginBottom: 6, height: 4, borderRadius: 2, overflow: "hidden" }}>
                          {[20, 40, 60, 80, 100].map((seg, i) => (
                            <div key={i} style={{ flex: 1, background: s >= seg - 19
                              ? (seg <= 30 ? S.danger : seg <= 50 ? S.warning : seg <= 70 ? "#E8C547" : S.neon)
                              : "#2A2D32", opacity: s >= seg - 19 ? 1 : 0.3 }} />
                          ))}
                        </div>
                        <div style={{ fontSize: 13, color: r.color, fontWeight: 600 }}>{r.label}</div>
                      </div>
                    );
                  })()}
                </div>

                {/* ACWR */}
                <div className="card">
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, gap: 8, flexWrap: "wrap" }}>
                    <div style={{ fontSize: 13, color: S.dim, fontWeight: 600, letterSpacing: ".08em", display: "flex", alignItems: "center" }}>
                      RIESGO · ACWR
                      <InfoTip text="Compara tu carga de entrenamiento de esta semana contra el promedio de las últimas 4. Entre 0.8x y 1.3x estás en zona segura: más alto sube el riesgo de lesión, más bajo significa que estás perdiendo la forma ganada." />
                    </div>
                    <span style={{ fontSize: 13, fontWeight: 700, color: acwrColor,
                      background: `${acwrColor}15`, border: `1px solid ${acwrColor}44`,
                      borderRadius: 20, padding: "3px 10px", letterSpacing: ".06em" }}>{acwrLabel}</span>
                  </div>
                  <div style={{ fontFamily: FONT_NUM, fontSize: 56, fontWeight: 900, color: acwrColor, lineHeight: 1 }}>
                    {acwrDisplay}
                  </div>
                  <ACWRSlider acwr={acwr} color={acwrColor} />
                  <div style={{ fontSize: 15, color: S.muted, marginTop: 12, lineHeight: 1.6 }}>
                    {pulse.injuryRisk?.action || pulse.warnings?.[0] || "Óptimo: 0.8 – 1.3x"}
                  </div>
                </div>
              </div>

              {/* Análisis */}
              <div className="card" style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 13, color: S.dim, fontWeight: 600, letterSpacing: ".08em", marginBottom: 14 }}>
                  ANÁLISIS PULSE
                </div>
                <div style={{ fontSize: 16, color: S.muted, lineHeight: 1.8, textAlign: "justify" }}>
                  {pulseExp ? pulse.aiVerdict : (pulse.aiVerdict || "").slice(0, 280) + ((pulse.aiVerdict || "").length > 280 ? "..." : "")}
                </div>
                {pulseExp && pulse.strengths?.length > 0 && (
                  <div style={{ marginTop: 16 }}>
                    <div style={{ fontSize: 13, color: S.neon, fontWeight: 600, letterSpacing: ".08em", marginBottom: 8 }}>FORTALEZAS</div>
                    {pulse.strengths.map((s, i) => (
                      <div key={i} style={{ display: "flex", gap: 8, marginBottom: 7 }}>
                        <span style={{ color: S.neon, fontSize: 15, flexShrink: 0 }}>✓</span>
                        <span style={{ fontSize: 15, color: S.muted, lineHeight: 1.5 }}>{s}</span>
                      </div>
                    ))}
                  </div>
                )}
                {pulseExp && pulse.warnings?.length > 0 && (
                  <div style={{ marginTop: 14 }}>
                    <div style={{ fontSize: 13, color: S.warning, fontWeight: 600, letterSpacing: ".08em", marginBottom: 8 }}>ALERTAS</div>
                    {pulse.warnings.map((w, i) => (
                      <div key={i} style={{ display: "flex", gap: 8, marginBottom: 7 }}>
                        <span style={{ color: S.warning, fontSize: 15, flexShrink: 0 }}>⚠</span>
                        <span style={{ fontSize: 15, color: S.muted, lineHeight: 1.5 }}>{w}</span>
                      </div>
                    ))}
                  </div>
                )}
                {(pulse.aiVerdict || "").length > 280 && (
                  <button onClick={() => setPulseExp(!pulseExp)} style={{
                    background: "none", border: "none", color: S.cobalt, fontSize: 14,
                    fontWeight: 600, marginTop: 12, padding: 0 }}>
                    {pulseExp ? "Ver menos ↑" : "Ver análisis completo ↓"}
                  </button>
                )}
              </div>

              {/* Siguiente sesión — full width */}
              {nextSession && (
                <div className="card" style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 13, color: S.dim, fontWeight: 600, letterSpacing: ".08em", marginBottom: 8 }}>
                    SIGUIENTE SESIÓN · {nextSession.day?.toUpperCase()}
                  </div>
                  <div style={{ fontFamily: FONT_NUM, fontSize: 28, fontWeight: 900, color: S.text, marginBottom: 4 }}>
                    {nextSession.type}
                  </div>
                  <div style={{ fontSize: 15, color: S.muted, marginBottom: 18, lineHeight: 1.6 }}>
                    {nextSession.km && nextSession.km !== "—" ? `${nextSession.km} · ` : ""}{nextSession.notes}
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 8 }}>
                    {restWeekPlan.map((p, i) => (
                      <div key={i} style={{ background: S.bg, border: `1px solid ${S.border}`,
                        borderRadius: 10, padding: "10px 12px", textAlign: "center" }}>
                        <div style={{ fontSize: 13, color: S.cobalt, fontWeight: 700, marginBottom: 3 }}>{p.day}</div>
                        <div style={{ fontSize: 13, color: S.muted, lineHeight: 1.4 }}>{p.type}</div>
                        {p.km && p.km !== "—" && (
                          <div style={{ fontFamily: FONT_NUM, fontSize: 14, fontWeight: 800, color: S.text, marginTop: 3 }}>{p.km}</div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Proyección */}
              {pulse.projectedTime && (
                <div className="card" style={{ background: "rgba(61,126,255,.07)", borderColor: "rgba(61,126,255,.2)" }}>
                  <div style={{ fontSize: 13, color: S.dim, fontWeight: 600, letterSpacing: ".08em", marginBottom: 10, display: "flex", alignItems: "center" }}>
                    PROYECCIÓN {meta.metaCarrera.nombre.toUpperCase()}
                    <InfoTip text="Tiempo estimado de meta según tu ritmo y volumen actuales de entrenamiento. Es una proyección, no una garantía — mejora si sigues el plan semanal." />
                  </div>
                  <div style={{ display: "flex", gap: 28, alignItems: "center", flexWrap: "wrap" }}>
                    <div>
                      <div style={{ fontFamily: FONT_NUM, fontSize: 48, fontWeight: 900, color: S.cobalt, lineHeight: 1 }}>
                        {pulse.projectedTime}
                      </div>
                      <div style={{ fontSize: 15, color: S.muted, marginTop: 4 }}>
                        Ritmo: <strong style={{ color: S.text }}>{pulse.projectedPace}/km</strong>
                      </div>
                    </div>
                    {pulse.funFact && (
                      <div style={{ flex: 1, minWidth: 200, fontSize: 15, color: S.muted, lineHeight: 1.7,
                        borderLeft: `2px solid ${S.border}`, paddingLeft: 20 }}>
                        🏃 {pulse.funFact}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ══ SEMANA ══ */}
          {tab === "SEMANA" && (
            <div>
              <h1 className="page-title">Tu semana</h1>

              {/* KM esta semana */}
              <div className="card" style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 13, color: S.dim, fontWeight: 600, letterSpacing: ".08em", marginBottom: 10, display: "flex", alignItems: "center" }}>
                  KILÓMETROS ESTA SEMANA
                  <InfoTip text="Total de kilómetros de running que acumulaste esta semana, y las barras de abajo muestran cómo se compara con tus últimas 8 semanas." />
                </div>
                <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 22, flexWrap: "wrap" }}>
                  <span style={{ fontFamily: FONT_NUM, fontSize: 60, fontWeight: 900, color: S.text, lineHeight: 1 }}>
                    {lastWeek.total_km}
                  </span>
                  <span style={{ fontSize: 18, color: S.muted }}>km</span>
                  <span style={{ fontSize: 14, fontWeight: 700, color: weeklyIncrease >= 0 ? S.neon : S.danger }}>
                    {incSign} vs. semana anterior
                  </span>
                </div>

                {/* Barras con valores */}
                <div style={{ display: "flex", alignItems: "flex-end", gap: 6, height: 112 }}>
                  {last8.map((w, i) => {
                    const isLast = i === last8.length - 1;
                    const hPx = Math.max((w.total_km / maxKm) * 78, 3);
                    const val = w.total_km >= 10 ? Math.round(w.total_km) : w.total_km.toFixed(1);
                    return (
                      <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column",
                        alignItems: "center", justifyContent: "flex-end", minWidth: 0 }}>
                        <div className="bar-val" style={{ fontFamily: FONT_NUM, fontSize: 13, fontWeight: 800,
                          color: isLast ? S.neon : S.muted, marginBottom: 4, whiteSpace: "nowrap" }}>
                          {val}
                        </div>
                        <div style={{ width: "100%", height: hPx,
                          background: isLast ? `linear-gradient(180deg, ${S.neon}, ${S.cobalt})` : "rgba(202,255,0,.16)",
                          cursor: "default", title: `${w.total_km} km · ${w.sessions} sesiones · ${fmtWeek(w.week)}`,
                          borderRadius: "4px 4px 0 0",
                          border: isLast ? "1px solid rgba(202,255,0,.4)" : "none" }} />
                      </div>
                    );
                  })}
                </div>
                <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                  {weekLabels.map((l, i) => (
                    <div key={i} className="bar-lbl" style={{ flex: 1, fontSize: 13, color: S.dim,
                      textAlign: "center", whiteSpace: "nowrap", overflow: "hidden" }}>{l}</div>
                  ))}
                </div>
              </div>

              {/* Métricas con tendencia */}
              <div className="grid3">
                {[
                  { label: "RITMO PROMEDIO (MIN/KM)", val: lastWeek.avg_pace.toFixed(2), color: S.cobalt,
                    data: last8.map(w => w.avg_pace), goodWhen: "down", decimals: 2,
                    tip: "Minutos por kilómetro promedio de tus sesiones de running esta semana, sin contar la carrera. Bajar el número es correr más rápido." },
                  { label: "FC PROMEDIO (BPM)", val: Math.round(lastWeek.avg_hr), color: S.warning,
                    data: last8.map(w => w.avg_hr), unit: "bpm", goodWhen: "down",
                    tip: "Pulsaciones por minuto promedio durante tus sesiones de running esta semana. Ayuda a ver si el esfuerzo percibido coincide con el esfuerzo real." },
                  { label: "SESIONES / SEMANA", val: lastWeek.sessions, color: S.neon,
                    data: last8.map(w => w.sessions), goodWhen: "up",
                    tip: "Cantidad de entrenamientos que registraste esta semana, sin importar la disciplina." },
                ].map((m, i) => (
                  <div key={i} className="card" style={{ display: "flex", flexDirection: "column", minHeight: 220 }}>
                    <div style={{ fontSize: 13, color: S.dim, fontWeight: 600, letterSpacing: ".08em", marginBottom: 6, display: "flex", alignItems: "center" }}>
                      {m.label}<InfoTip text={m.tip} />
                    </div>
                    <div style={{ fontFamily: FONT_NUM, fontSize: 34, fontWeight: 900, color: m.color, lineHeight: 1, marginBottom: 4 }}>
                      {m.val}
                    </div>
                    <TrendChart data={m.data} labels={weekLabels} color={m.color}
                      goodWhen={m.goodWhen} unit={m.unit || ""} decimals={m.decimals || 0} />
                  </div>
                ))}
              </div>

              {/* Sesiones recientes */}
              <div className="card">
                <div style={{ fontSize: 13, color: S.dim, fontWeight: 600, letterSpacing: ".08em", marginBottom: 6 }}>
                  SESIONES RECIENTES
                </div>
                {recentSessions.map((a, i) => {
                  const tagColors = { LARGO: S.neon, INTERVALOS: S.cobalt, TEMPO: S.danger, RODAJE: S.text };
                  return (
                    <div key={i} style={{ display: "flex", alignItems: "center", gap: 14,
                      padding: "14px 0",
                      borderBottom: i < recentSessions.length - 1 ? `1px solid ${S.border}` : "none" }}>
                      <div className="session-date">{fmtDate(a.date)}</div>
                      <TagBadge tag={a.tag} />
                      <div style={{ flex: 1, fontSize: 15, color: S.text, minWidth: 0,
                        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {a.name}
                      </div>
                      <div style={{ fontFamily: FONT_NUM, fontSize: 18, fontWeight: 800,
                        color: tagColors[a.tag] || S.text, whiteSpace: "nowrap" }}>
                        {a.dist_km}<span style={{ fontSize: 13, color: S.dim, marginLeft: 2 }}>km</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* ══ CARGA ══ */}
          {tab === "CARGA" && (
            <div>
              <h1 className="page-title">Carga y prevención</h1>

              {/* Explicación ACWR */}
              <div className="card" style={{ marginBottom: 16, padding: "16px 20px" }}>
                <div style={{ fontSize: 16, color: S.muted, lineHeight: 1.7 }}>
                  <strong style={{ color: S.text }}>¿Qué es el ACWR?</strong> Compara tu carga semanal
                  (minutos totales de entrenamiento) de tu última semana (carga aguda) contra el
                  promedio de tus últimas 4 semanas (carga crónica). Entre{" "}
                  <strong style={{ color: S.neon }}>0.8x y 1.3x</strong> estás
                  en la zona óptima. Por encima, el aumento brusco de carga eleva el riesgo de lesión;
                  por debajo, pierdes las adaptaciones que ya habías ganado.
                </div>
              </div>

              <div className="grid3">
                <div className="card">
                  <div style={{ fontSize: 13, color: S.dim, fontWeight: 600, letterSpacing: ".08em", marginBottom: 8, display: "flex", alignItems: "center" }}>
                    CARGA · ACWR
                    <InfoTip text="Compara tu carga de entrenamiento de esta semana contra el promedio de las últimas 4. Entre 0.8x y 1.3x estás en zona segura: más alto sube el riesgo de lesión, más bajo significa que estás perdiendo la forma ganada." />
                  </div>
                  <div style={{ fontFamily: FONT_NUM, fontSize: 44, fontWeight: 900, color: acwrColor, lineHeight: 1 }}>
                    {acwrDisplay}
                  </div>
                  <ACWRSlider acwr={acwr} color={acwrColor} />
                  <div style={{ fontSize: 14, color: S.muted, marginTop: 10 }}>Óptimo: 0.8 – 1.3x</div>
                </div>

                <div className="card">
                  <div style={{ fontSize: 15, fontWeight: 700, color: S.text, marginBottom: 8, display: "flex", alignItems: "center" }}>
                    Incremento semanal
                    <InfoTip text="Cuánto subió o bajó tu volumen de running frente a la semana anterior. Subir más de 10% por semana eleva el riesgo de lesión." />
                  </div>
                  <div style={{ fontFamily: FONT_NUM, fontSize: 40, fontWeight: 900, lineHeight: 1,
                    color: weeklyIncrease > 15 ? S.danger : weeklyIncrease > 10 ? S.warning : weeklyIncrease < 0 ? S.danger : S.neon }}>
                    {incSign}
                  </div>
                  <div style={{ fontSize: 15, color: S.muted, marginTop: 10 }}>
                    De {prevWeek?.total_km ?? "—"} km a {lastWeek.total_km} km
                  </div>
                  <div style={{ fontSize: 13, color: S.warning, marginTop: 4 }}>Recomendado: &lt; 10% / semana</div>
                </div>

                <div className="card">
                  <div style={{ fontSize: 15, fontWeight: 700, color: S.text, marginBottom: 8, display: "flex", alignItems: "center" }}>
                    Balance 80/20
                    <InfoTip text="Proporción de tus sesiones en ritmo fácil vs. exigente. La regla 80/20 dice que el 80% de tu entrenamiento debería ser a ritmo cómodo para rendir mejor en el 20% restante." />
                  </div>
                  <div style={{ fontFamily: FONT_NUM, fontSize: 40, fontWeight: 900, color: S.warning, lineHeight: 1 }}>
                    {balanceMetric?.value || "—"}
                  </div>
                  <div style={{ fontSize: 15, color: S.muted, marginTop: 10 }}>
                    {balanceMetric?.note || "Proporción de sesiones fáciles"}
                  </div>
                  <div style={{ fontSize: 13, color: S.warning, marginTop: 4 }}>Ideal: ≥ 80% en zona baja</div>
                </div>
              </div>

              {/* Plan taper */}
              {taper && taper.length > 0 && (
                <div className="card">
                  <div style={{ fontSize: 13, color: S.dim, fontWeight: 600, letterSpacing: ".08em", marginBottom: 18 }}>
                    PLAN DE TAPER · {daysLeft} DÍAS
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: `repeat(auto-fit, minmax(200px, 1fr))`, gap: 12 }}>
                    {taper.map((t, i) => (
                      <div key={i} style={{ background: S.bg, border: `1px solid ${t.color || "#2A2D32"}22`,
                        borderTop: `3px solid ${t.color || "#2A2D32"}`, borderRadius: "0 0 12px 12px", padding: 16 }}>
                        <div style={{ fontSize: 13, fontWeight: 700, color: t.color || S.muted, marginBottom: 4 }}>{t.label}</div>
                        <div style={{ fontSize: 13, color: S.dim, marginBottom: 12 }}>{t.week}</div>
                        <div style={{ fontFamily: FONT_NUM, fontSize: 28, fontWeight: 900, color: S.text, lineHeight: 1 }}>
                          {t.km}<span style={{ fontSize: 13, color: S.muted, marginLeft: 4 }}>km</span>
                        </div>
                        <div style={{ fontSize: 13, color: S.muted, marginTop: 4 }}>{t.sessions} sesiones</div>
                        <div style={{ fontSize: 15, color: S.muted, marginTop: 8, lineHeight: 1.5 }}>{t.focus}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

        </div>
      </div>
    </div>
  );
}
// práctica de git para entrevista