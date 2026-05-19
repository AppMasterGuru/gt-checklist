import { useState, useEffect, useRef } from 'react';
import Head from 'next/head';

// ── Brand colours ──────────────────────────────────────────────────────────────
const C = {
  navy:    '#1B3A6B',
  orange:  '#E8471C',
  green:   '#2D7D46',
  navyBg:  '#EEF1F7',
  orangeBg:'#FDF0EC',
  greenBg: '#EAF4EE',
};

// ── Baseline deliverables from Propuesta_Pipeline1_V2_2.md (signed 2026-05-11) ─
// Covers §2.1 (functional flow), §2.2 (languages), §2.4 (infrastructure), §2.5 (kickers)
const BASELINE = [
  // §2.1 — Flujo funcional (8 items)
  { id: 'bl-1',  section: '§2.1', name: 'Recepción de solicitudes por correo electrónico y WhatsApp',        done: true  },
  { id: 'bl-2',  section: '§2.1', name: 'Consulta de tarifas a aerolíneas y navieras',                        done: true  },
  { id: 'bl-3',  section: '§2.1', name: 'Lectura de costos de aduana desde Google Drive',                     done: true  },
  { id: 'bl-4',  section: '§2.1', name: 'Lectura de tarifas de transporte local desde base de datos interna', done: true  },
  { id: 'bl-5',  section: '§2.1', name: 'Generación de proforma Excel con el formato de Global Transport',    done: true  },
  { id: 'bl-6',  section: '§2.1', name: 'Generación de PDF con logos, plantillas y firma corporativa',        done: true  },
  { id: 'bl-7',  section: '§2.1', name: 'Compuerta de aprobación humana antes de cualquier envío al cliente', done: true  },
  { id: 'bl-8',  section: '§2.1', name: 'Envío automático desde correo corporativo con firma del responsable', done: false },
  // §2.2 — Idiomas
  { id: 'bl-9',  section: '§2.2', name: 'Proformas y PDFs en español e inglés',                               done: true  },
  // §2.4 — Infraestructura técnica (6 items)
  { id: 'bl-10', section: '§2.4', name: 'Integración con Google Drive de Global Transport',                    done: true  },
  { id: 'bl-11', section: '§2.4', name: 'Estructuración de tarifarios y plantillas de costos de aduana',      done: true  },
  { id: 'bl-12', section: '§2.4', name: 'Codificación de procedimientos SIG ISO 9001 en la lógica del agente', done: false },
  { id: 'bl-13', section: '§2.4', name: 'Manejo de credenciales con prácticas compatibles con BASC/ISO',      done: true  },
  { id: 'bl-14', section: '§2.4', name: 'Interfaz de aprobación humana para el equipo comercial',             done: true  },
  { id: 'bl-15', section: '§2.4', name: 'Listeners de correo y WhatsApp con detección automática de idioma',  done: false },
  // §2.5a — Piloto WCA (3 items)
  { id: 'bl-16', section: '§2.5a', name: 'Capabilities deck bilingüe',                                        done: false },
  { id: 'bl-17', section: '§2.5a', name: 'Correos de outreach localizados',                                   done: false },
  { id: 'bl-18', section: '§2.5a', name: 'Envío a 20–30 agentes WCA seleccionados del directorio',            done: false },
  // §2.5b — Auto-acuse
  { id: 'bl-19', section: '§2.5b', name: 'Auto-acuse de recibo multilingüe',                                  done: false },
  // Tests
  { id: 'bl-20', section: 'Tests', name: 'Tests automatizados',                                               done: true  },
];

// ── Status tags ────────────────────────────────────────────────────────────────
const TAG_CONFIG = {
  ready:   { label: 'ready to build', bg: '#e6f4ea', color: '#1e7e34' },
  client:  { label: 'needs client',   bg: '#e8f0fe', color: '#1a56db' },
  blocked: { label: 'blocked',        bg: '#fdecea', color: '#b91c1c' },
};

function Tag({ tag }) {
  const cfg = TAG_CONFIG[tag];
  if (!cfg) return null;
  return (
    <span style={{
      display: 'inline-block', fontSize: '10px', fontWeight: 600,
      letterSpacing: '.04em', textTransform: 'uppercase',
      padding: '2px 7px', borderRadius: '20px',
      background: cfg.bg, color: cfg.color,
      marginLeft: '8px', verticalAlign: 'middle', whiteSpace: 'nowrap',
    }}>{cfg.label}</span>
  );
}

// ── Colour tag badge (ADDED / EXTRA) ───────────────────────────────────────────
function ColorBadge({ colorTag }) {
  if (!colorTag) return null;
  const isAdded = colorTag === 'added';
  return (
    <span style={{
      display: 'inline-block', fontSize: '9px', fontWeight: 700,
      letterSpacing: '.08em', textTransform: 'uppercase',
      padding: '2px 7px', borderRadius: '20px',
      background: isAdded ? C.orangeBg : C.greenBg,
      color: isAdded ? C.orange : C.green,
      marginLeft: '8px', verticalAlign: 'middle', whiteSpace: 'nowrap',
      border: `1px solid ${isAdded ? C.orange : C.green}`,
    }}>{isAdded ? 'ADDED' : 'EXTRA'}</span>
  );
}

// ── Row left-border colour from colorTag ──────────────────────────────────────
function rowAccent(colorTag) {
  if (colorTag === 'added') return C.orange;
  if (colorTag === 'extra') return C.green;
  return 'transparent';
}

// ── PDF export for Delta tab ───────────────────────────────────────────────────
function exportDeltaPDF(deltaItems) {
  const today = new Date().toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  const rows = deltaItems.map(t => {
    const type = t.colorTag === 'added' ? 'ADDED' : 'EXTRA';
    const typeColor = t.colorTag === 'added' ? C.orange : C.green;
    const status = t.done ? '✓ Done' : '○ Pending';
    return `
      <tr>
        <td style="padding:10px 14px;border-bottom:1px solid #e5e7eb;">
          <span style="font-size:10px;font-weight:700;padding:2px 8px;border-radius:20px;
            background:${t.colorTag === 'added' ? '#FDF0EC' : '#EAF4EE'};
            color:${typeColor};border:1px solid ${typeColor};">${type}</span>
        </td>
        <td style="padding:10px 14px;border-bottom:1px solid #e5e7eb;font-size:13px;color:#111;">${t.name}</td>
        <td style="padding:10px 14px;border-bottom:1px solid #e5e7eb;font-size:12px;color:#6b7280;white-space:nowrap;">${t.section || ''}</td>
        <td style="padding:10px 14px;border-bottom:1px solid #e5e7eb;font-size:12px;color:${t.done ? '#2D7D46' : '#9ca3af'};">${status}</td>
      </tr>`;
  }).join('');

  const html = `<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<title>GT Pipeline #1 — Delta Report ${today}</title>
<style>
  body{font-family:'Helvetica Neue',Arial,sans-serif;margin:40px;color:#111;}
  h1{font-size:20px;color:#1B3A6B;margin-bottom:4px;}
  .sub{font-size:12px;color:#6b7280;margin-bottom:24px;}
  table{width:100%;border-collapse:collapse;}
  th{text-align:left;padding:8px 14px;font-size:11px;text-transform:uppercase;
     letter-spacing:.06em;color:#6b7280;border-bottom:2px solid #1B3A6B;}
  .footer{margin-top:32px;font-size:11px;color:#9ca3af;text-align:center;}
</style>
</head><body>
  <h1>Global Transport × TimeBack AI — Pipeline #1</h1>
  <div class="sub">Delta Report · ${today} · Scope additions &amp; extras beyond baseline</div>
  <table>
    <thead><tr>
      <th>Type</th><th>Task</th><th>Section</th><th>Status</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>
  <div class="footer">TimeBack AI · Baseline signed 2026-05-11</div>
</body></html>`;

  const win = window.open('', '_blank');
  win.document.write(html);
  win.document.close();
  win.print();
}

// ── Collapsible section component ─────────────────────────────────────────────
function Section({ sec, sIdx, colorMode }) {
  const [open, setOpen] = useState(true);
  const [expandedId, setExpandedId] = useState(null);
  const tasks = sec.tasks;
  const secDone = tasks.filter(t => t.done).length;
  const secBlocked = tasks.filter(t => t.tags?.includes('blocked') && !t.done).length;
  const allDone = secDone === tasks.length;

  return (
    <div style={{ marginBottom: '12px', background: '#fff', border: '1px solid #e5e7eb', borderRadius: '12px', overflow: 'hidden' }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', gap: '12px',
          padding: '14px 18px', background: 'transparent', border: 'none',
          cursor: 'pointer', textAlign: 'left',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flex: 1, minWidth: 0 }}>
          <span style={{ fontFamily: 'var(--mono)', fontSize: '10px', fontWeight: 500, letterSpacing: '.08em', textTransform: 'uppercase', color: allDone ? '#16a34a' : '#6b7280' }}>
            {String(sIdx + 1).padStart(2, '0')}
          </span>
          <span style={{ fontSize: '14px', fontWeight: 500, color: '#111' }}>{sec.title}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0 }}>
          <span style={{ fontSize: '11px', fontFamily: 'var(--mono)', fontWeight: 500, color: allDone ? '#16a34a' : secBlocked > 0 ? '#b91c1c' : '#6b7280' }}>
            {secDone}/{tasks.length}
          </span>
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .2s', color: '#9ca3af' }}>
            <path d="M2 5l5 5 5-5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      </button>

      {open && (
        <div style={{ borderTop: '1px solid #f3f4f6' }}>
          {tasks.map((task, tIdx) => {
            const done = task.done;
            const isExpanded = expandedId === task.id;
            const accent = rowAccent(task.colorTag);
            return (
              <div key={task.id} style={{ borderBottom: tIdx < tasks.length - 1 ? '1px solid #f3f4f6' : 'none', borderLeft: task.colorTag ? `4px solid ${accent}` : '4px solid transparent' }}>
                <div style={{ display: 'flex', alignItems: 'flex-start' }}>
                  <div style={{ padding: '13px 12px 13px 15px', display: 'flex', alignItems: 'flex-start', paddingTop: '15px', flexShrink: 0 }}>
                    <input type="checkbox" checked={done} readOnly style={{ width: '15px', height: '15px', cursor: 'default', accentColor: C.navy }} />
                  </div>
                  <div
                    onClick={() => setExpandedId(id => id === task.id ? null : task.id)}
                    style={{
                      flex: 1, padding: '12px 18px 12px 4px', cursor: 'pointer',
                      background: isExpanded ? '#f8f6f2' : done ? '#fafaf9' : 'transparent',
                      transition: 'background .12s', minWidth: 0,
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '8px' }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: '13.5px', color: done ? '#9ca3af' : '#111', textDecoration: done ? 'line-through' : 'none', lineHeight: 1.5 }}>
                          {task.name}
                          {colorMode && <ColorBadge colorTag={task.colorTag} />}
                          {(task.tags || []).map(tag => <Tag key={tag} tag={tag} />)}
                        </div>
                        {task.sub && (
                          <div style={{ fontSize: '11.5px', color: '#9ca3af', marginTop: '3px', lineHeight: 1.5 }}>{task.sub}</div>
                        )}
                      </div>
                      <svg width="12" height="12" viewBox="0 0 12 12" fill="none" style={{ transform: isExpanded ? 'rotate(180deg)' : 'none', transition: 'transform .2s', color: '#d1d5db', flexShrink: 0, marginTop: '4px' }}>
                        <path d="M2 4l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </div>
                  </div>
                </div>
                {isExpanded && (
                  <div style={{ background: '#f8f6f2', borderTop: '1px solid #ede9e2', padding: '14px 18px' }}>
                    {!task.why && (!task.how || task.how.length === 0) ? (
                      <div style={{ fontSize: '12px', color: '#9ca3af', fontFamily: 'var(--mono)' }}>
                        {task.id} · {task.section}
                      </div>
                    ) : (
                      <>
                        {task.why && task.why !== 'Completed.' && (
                          <div style={{ marginBottom: '14px' }}>
                            <div style={{ fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.07em', color: C.orange, marginBottom: '6px', fontFamily: 'var(--mono)' }}>Why this matters</div>
                            <p style={{ fontSize: '13px', color: '#374151', lineHeight: 1.65 }}>{task.why}</p>
                          </div>
                        )}
                        {task.how?.length > 0 && !(task.how.length === 1 && task.how[0].startsWith('Done')) && (
                          <div>
                            <div style={{ fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.07em', color: C.navy, marginBottom: '8px', fontFamily: 'var(--mono)' }}>How to do it</div>
                            <ol style={{ paddingLeft: '1.2rem' }}>
                              {task.how.map((step, i) => (
                                <li key={i} style={{ marginBottom: '6px', fontSize: '13px', lineHeight: 1.6, color: '#374151' }}>{step}</li>
                              ))}
                            </ol>
                          </div>
                        )}
                        {task.why === 'Completed.' && (
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <span style={{ fontSize: '18px', color: '#16a34a' }}>✓</span>
                            <span style={{ fontSize: '13px', color: '#16a34a', fontWeight: 500 }}>{task.how?.[0] || 'Completed'}</span>
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Baseline tab ───────────────────────────────────────────────────────────────
function BaselineTab() {
  const done = BASELINE.filter(t => t.done).length;
  const pct = Math.round((done / BASELINE.length) * 100);
  return (
    <div>
      <div style={{ marginBottom: '20px', padding: '12px 16px', background: C.navyBg, borderRadius: '8px', border: `1px solid ${C.navy}30` }}>
        <div style={{ fontFamily: 'var(--mono)', fontSize: '11px', color: C.navy, marginBottom: '4px', fontWeight: 600 }}>
          BASELINE — Propuesta Pipeline #1
        </div>
        <div style={{ fontSize: '12px', color: '#374151' }}>
          Firmada 2026-05-11 · {done}/{BASELINE.length} entregables completos ({pct}%) · §2.1 + §2.2 + §2.4 + §2.5 · Lista congelada — no cambia
        </div>
      </div>

      <div style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: '12px', overflow: 'hidden' }}>
        {BASELINE.map((item, i) => (
          <div key={item.id} style={{
            display: 'flex', alignItems: 'center', gap: '12px',
            padding: '11px 18px',
            borderBottom: i < BASELINE.length - 1 ? '1px solid #f3f4f6' : 'none',
            borderLeft: `4px solid ${C.navy}`,
            background: item.done ? '#fafaf9' : '#fff',
          }}>
            <span style={{ fontFamily: 'var(--mono)', fontSize: '9px', color: '#d1d5db', width: '28px', flexShrink: 0, textAlign: 'right' }}>
              {item.section}
            </span>
            <input type="checkbox" checked={item.done} readOnly style={{ width: '14px', height: '14px', cursor: 'default', accentColor: C.navy, flexShrink: 0 }} />
            <span style={{ fontSize: '13px', color: item.done ? '#9ca3af' : '#111', textDecoration: item.done ? 'line-through' : 'none', flex: 1, lineHeight: 1.4 }}>
              {item.name}
            </span>
            {item.done && (
              <span style={{ fontFamily: 'var(--mono)', fontSize: '10px', color: '#16a34a', fontWeight: 600, flexShrink: 0 }}>✓</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Delta tab ──────────────────────────────────────────────────────────────────
function DeltaTab({ sections }) {
  const allTasks = sections.flatMap(s => s.tasks.map(t => ({ ...t, section: s.title })));
  const deltaItems = allTasks.filter(t => t.colorTag === 'added' || t.colorTag === 'extra');
  const addedItems = deltaItems.filter(t => t.colorTag === 'added');
  const extraItems = deltaItems.filter(t => t.colorTag === 'extra');
  const today = new Date().toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });

  if (deltaItems.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '48px 24px', color: '#9ca3af', fontFamily: 'var(--mono)', fontSize: '12px' }}>
        No delta items yet. Tag tasks with [ADDED] or [EXTRA] in MEMORY.md to track scope changes.
      </div>
    );
  }

  return (
    <div>
      {/* Delta header bar */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
        <div style={{ padding: '12px 16px', background: '#fff', borderRadius: '8px', border: '1px solid #e5e7eb', flex: 1, marginRight: '12px' }}>
          <div style={{ fontFamily: 'var(--mono)', fontSize: '11px', color: '#6b7280', marginBottom: '4px' }}>DELTA REPORT · {today}</div>
          <div style={{ fontSize: '12px', color: '#374151' }}>
            <span style={{ color: C.orange, fontWeight: 600 }}>{addedItems.length} scope additions</span>
            {extraItems.length > 0 && <> · <span style={{ color: C.green, fontWeight: 600 }}>{extraItems.length} extras</span></>}
            {' '}beyond signed baseline
          </div>
        </div>
        <button
          onClick={() => exportDeltaPDF(deltaItems)}
          style={{
            padding: '10px 18px', background: C.navy, color: '#fff',
            border: 'none', borderRadius: '8px', cursor: 'pointer',
            fontFamily: 'var(--mono)', fontSize: '11px', fontWeight: 600,
            letterSpacing: '.04em', textTransform: 'uppercase', whiteSpace: 'nowrap',
            flexShrink: 0,
          }}
        >
          Export PDF
        </button>
      </div>

      {/* Legend */}
      <div style={{ display: 'flex', gap: '12px', marginBottom: '16px' }}>
        {[
          { color: C.orange, bg: C.orangeBg, label: 'ADDED — scope beyond signed proposal' },
          { color: C.green,  bg: C.greenBg,  label: 'EXTRA — bonus delivered at no charge' },
        ].map(({ color, bg, label }) => (
          <div key={label} style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: '#6b7280' }}>
            <span style={{ width: '10px', height: '10px', borderRadius: '2px', background: bg, border: `1px solid ${color}`, display: 'inline-block' }} />
            {label}
          </div>
        ))}
      </div>

      {/* Delta items */}
      <div style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: '12px', overflow: 'hidden' }}>
        {deltaItems.map((task, i) => (
          <div key={task.id} style={{
            display: 'flex', alignItems: 'center', gap: '12px',
            padding: '12px 18px',
            borderBottom: i < deltaItems.length - 1 ? '1px solid #f3f4f6' : 'none',
            borderLeft: `4px solid ${task.colorTag === 'added' ? C.orange : C.green}`,
          }}>
            <ColorBadge colorTag={task.colorTag} />
            <span style={{ fontSize: '13.5px', color: task.done ? '#9ca3af' : '#111', textDecoration: task.done ? 'line-through' : 'none', flex: 1 }}>
              {task.name}
            </span>
            <span style={{ fontFamily: 'var(--mono)', fontSize: '10px', color: '#9ca3af', whiteSpace: 'nowrap' }}>
              {task.section?.slice(0, 28)}
            </span>
            <span style={{ fontFamily: 'var(--mono)', fontSize: '10px', color: task.done ? '#16a34a' : '#9ca3af', flexShrink: 0 }}>
              {task.done ? '✓ done' : '○ pending'}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main dashboard ─────────────────────────────────────────────────────────────
export default function Dashboard() {
  const [sections, setSections] = useState([]);
  const [tab, setTab] = useState('current'); // 'current' | 'baseline' | 'delta'
  const [loading, setLoading] = useState(true);
  const [memoryLastModified, setMemoryLastModified] = useState(null);
  const [memoryError, setMemoryError] = useState(null);
  const [agentLog, setAgentLog] = useState([]);

  async function loadMemory() {
    setMemoryError(null);
    try {
      const r = await fetch('/api/memory');
      const data = await r.json();
      if (!r.ok || !data.sections?.length) {
        setMemoryError(data.error || 'No data in Redis. Run: python3 scripts/sync_checklist.py');
        setSections([]);
      } else {
        setSections(data.sections);
        setMemoryLastModified(data.lastModified);
      }
    } catch (err) {
      setMemoryError(err.message);
      setSections([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadMemory();
    fetch('/api/agent-log')
      .then(r => r.ok ? r.json() : { log: [] })
      .then(({ log }) => setAgentLog(log || []))
      .catch(() => {});
    const interval = setInterval(() => {
      loadMemory();
      fetch('/api/agent-log').then(r => r.ok ? r.json() : { log: [] }).then(({ log }) => setAgentLog(log || [])).catch(() => {});
    }, 30000);
    return () => clearInterval(interval);
  }, []);

  const allTasks    = sections.flatMap(s => s.tasks);
  const doneCount   = allTasks.filter(t => t.done).length;
  const total       = allTasks.length;
  const pct         = total ? Math.round((doneCount / total) * 100) : 0;
  const blockedCount = allTasks.filter(t => t.tags?.includes('blocked') && !t.done).length;
  const readyCount  = allTasks.filter(t => t.tags?.includes('ready') && !t.done).length;
  const deltaCount  = allTasks.filter(t => t.colorTag === 'added' || t.colorTag === 'extra').length;

  if (loading) return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f8f7f4' }}>
      <div style={{ fontSize: '13px', color: '#6b7280', letterSpacing: '.06em', textTransform: 'uppercase' }}>Loading checklist…</div>
    </div>
  );

  const TABS = [
    { id: 'current',  label: 'Current' },
    { id: 'baseline', label: 'Baseline' },
    { id: 'delta',    label: `Delta${deltaCount > 0 ? ` (${deltaCount})` : ''}` },
  ];

  return (
    <>
      <Head>
        <title>GT × TimeBack AI — Pipeline #1</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Sora:wght@400;500;600&display=swap" rel="stylesheet" />
        <style>{`
          *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
          :root { --font: 'Sora', sans-serif; --mono: 'IBM Plex Mono', monospace; }
          body { background: #f8f7f4; font-family: var(--font); color: #111; -webkit-font-smoothing: antialiased; }
          input[type=checkbox] { accent-color: #1B3A6B; flex-shrink: 0; }
          .section-hdr:hover { background: #f0ede8 !important; }
          .task-body-click:hover { background: #f5f3ef !important; }
          .tab-btn:hover { background: #f0ede8 !important; }
          @keyframes slideDown { from { opacity:0; transform:translateY(-4px); } to { opacity:1; transform:translateY(0); } }
          .detail-panel { animation: slideDown .18s ease both; }
          ol { padding-left: 1.2rem; }
          ol li { margin-bottom: 6px; font-size: 13px; line-height: 1.6; color: #374151; }
        `}</style>
      </Head>

      <div style={{ minHeight: '100vh', background: '#f8f7f4' }}>
        {/* Top bar */}
        <div style={{ background: C.navy, padding: '0 24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', height: '52px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: C.orange }} />
            <span style={{ fontFamily: 'var(--mono)', fontSize: '12px', color: 'rgba(255,255,255,.7)', letterSpacing: '.06em' }}>
              GLOBAL TRANSPORT × TIMEBACK AI
            </span>
          </div>
          <span style={{ fontFamily: 'var(--mono)', fontSize: '11px', color: 'rgba(255,255,255,.45)' }}>
            pipeline #1
          </span>
        </div>

        <div style={{ maxWidth: '780px', margin: '0 auto', padding: '32px 24px 64px' }}>
          {/* Header */}
          <div style={{ marginBottom: '24px' }}>
            <h1 style={{ fontSize: '28px', fontWeight: 600, color: '#111', lineHeight: 1.2, marginBottom: '6px' }}>
              Cotizador — master checklist
            </h1>
            <p style={{ fontSize: '14px', color: '#6b7280' }}>
              Pipeline #1 · Live from MEMORY.md
              {memoryLastModified && ` · ${new Date(memoryLastModified).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}`}
            </p>
          </div>

          {/* Error bar */}
          {memoryError && (
            <div style={{ marginBottom: '16px', padding: '12px 16px', borderRadius: '8px', background: '#fdecea', border: '1px solid #f5c6c2' }}>
              <div style={{ fontFamily: 'var(--mono)', fontSize: '11px', color: '#b91c1c', marginBottom: '6px' }}>✕ Dashboard not synced</div>
              <div style={{ fontSize: '12px', color: '#7f1d1d', lineHeight: 1.5 }}>
                Run <code style={{ background: '#fee2e2', padding: '1px 5px', borderRadius: '3px' }}>python3 scripts/sync_checklist.py</code> from the <code style={{ background: '#fee2e2', padding: '1px 5px', borderRadius: '3px' }}>cotizador/</code> directory.
              </div>
            </div>
          )}

          {/* Stats bar — 5 cards */}
          {total > 0 && (
            <>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '10px', marginBottom: '24px' }}>
                {[
                  { label: 'Done',     value: doneCount,    accent: C.navy },
                  { label: 'Total',    value: total,        accent: '#374151' },
                  { label: 'Complete', value: `${pct}%`,    accent: pct === 100 ? '#16a34a' : C.navy },
                  { label: 'Blocked',  value: blockedCount, accent: '#b91c1c' },
                  { label: 'Delta',    value: deltaCount,   accent: deltaCount > 0 ? C.orange : '#9ca3af' },
                ].map(({ label, value, accent }) => (
                  <div key={label} style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: '10px', padding: '14px 10px', textAlign: 'center' }}>
                    <div style={{ fontSize: '24px', fontWeight: 600, color: accent, fontFamily: 'var(--mono)', lineHeight: 1 }}>{value}</div>
                    <div style={{ fontSize: '10px', color: '#9ca3af', marginTop: '4px', textTransform: 'uppercase', letterSpacing: '.06em' }}>{label}</div>
                  </div>
                ))}
              </div>

              {/* Progress bar */}
              <div style={{ background: '#e5e7eb', borderRadius: '4px', height: '5px', marginBottom: '8px' }}>
                <div style={{ background: C.navy, borderRadius: '4px', height: '5px', width: `${pct}%`, transition: 'width .5s ease' }} />
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '28px' }}>
                <span style={{ fontSize: '11px', color: '#9ca3af', fontFamily: 'var(--mono)' }}>{readyCount} tasks ready to build now</span>
                <span style={{ fontSize: '11px', color: '#9ca3af', fontFamily: 'var(--mono)' }}>{blockedCount} blocked on client</span>
              </div>
            </>
          )}

          {/* Tab bar */}
          <div style={{ display: 'flex', gap: '4px', marginBottom: '20px', background: '#fff', border: '1px solid #e5e7eb', borderRadius: '10px', padding: '4px' }}>
            {TABS.map(t => (
              <button
                key={t.id}
                className="tab-btn"
                onClick={() => setTab(t.id)}
                style={{
                  flex: 1, padding: '8px 12px', border: 'none', borderRadius: '7px', cursor: 'pointer',
                  fontSize: '13px', fontWeight: tab === t.id ? 600 : 400,
                  background: tab === t.id ? C.navy : 'transparent',
                  color: tab === t.id ? '#fff' : '#6b7280',
                  transition: 'all .15s',
                }}
              >
                {t.label}
              </button>
            ))}
          </div>

          {/* Colour legend — shown on Current tab */}
          {tab === 'current' && deltaCount > 0 && (
            <div style={{ display: 'flex', gap: '16px', marginBottom: '16px', padding: '10px 14px', background: '#fff', border: '1px solid #e5e7eb', borderRadius: '8px' }}>
              <span style={{ fontSize: '11px', color: '#9ca3af', fontFamily: 'var(--mono)', marginRight: '4px' }}>LEGEND:</span>
              {[
                { color: C.orange, bg: C.orangeBg, label: 'ADDED — scope addition' },
                { color: C.green,  bg: C.greenBg,  label: 'EXTRA — bonus delivered' },
                { color: C.navy,   bg: C.navyBg,   label: 'default — baseline scope' },
              ].map(({ color, bg, label }) => (
                <div key={label} style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: '#6b7280' }}>
                  <span style={{ width: '10px', height: '10px', borderRadius: '2px', background: bg, border: `1px solid ${color}`, display: 'inline-block', flexShrink: 0 }} />
                  {label}
                </div>
              ))}
            </div>
          )}

          {/* Tab content */}
          {tab === 'current' && (
            <div>
              {sections.length === 0 && !memoryError && (
                <div style={{ textAlign: 'center', padding: '48px', color: '#9ca3af', fontFamily: 'var(--mono)', fontSize: '12px' }}>
                  No tasks found. Run sync_checklist.py to populate.
                </div>
              )}
              {sections.map((sec, i) => (
                <Section key={sec.id} sec={sec} sIdx={i} colorMode={true} />
              ))}
            </div>
          )}

          {tab === 'baseline' && <BaselineTab />}

          {tab === 'delta' && <DeltaTab sections={sections} />}

          {/* Agent activity feed — only on Current tab */}
          {tab === 'current' && agentLog.length > 0 && (
            <div style={{ marginTop: '40px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
                <div style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#2DD4BF' }} />
                <span style={{ fontFamily: 'var(--mono)', fontSize: '11px', color: '#6b7280', letterSpacing: '.06em', textTransform: 'uppercase' }}>Agent activity</span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {agentLog.slice(0, 10).map((entry, i) => (
                  <div key={i} style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: '10px', padding: '12px 16px' }}>
                    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px', marginBottom: entry.summary ? '6px' : 0 }}>
                      <div style={{ flex: 1 }}>
                        <span style={{ fontFamily: 'var(--mono)', fontSize: '11px', color: '#9ca3af' }}>
                          {new Date(entry.timestamp).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}
                        </span>
                        {entry.from_email && (
                          <span style={{ fontFamily: 'var(--mono)', fontSize: '11px', color: '#6b7280', marginLeft: '8px' }}>{entry.from_email}</span>
                        )}
                        {entry.subject && (
                          <div style={{ fontSize: '13px', fontWeight: 500, color: '#111', marginTop: '3px' }}>{entry.subject}</div>
                        )}
                      </div>
                      {entry.task_updates?.length > 0 && (
                        <span style={{ fontFamily: 'var(--mono)', fontSize: '10px', fontWeight: 600, letterSpacing: '.04em', textTransform: 'uppercase', padding: '2px 8px', borderRadius: '20px', background: '#e6f4ea', color: '#1e7e34', whiteSpace: 'nowrap', flexShrink: 0 }}>
                          {entry.task_updates.length} task{entry.task_updates.length > 1 ? 's' : ''} updated
                        </span>
                      )}
                    </div>
                    {entry.summary && (
                      <p style={{ fontSize: '13px', color: '#374151', lineHeight: 1.5, margin: 0 }}>{entry.summary}</p>
                    )}
                    {entry.notes?.length > 0 && (
                      <ul style={{ margin: '6px 0 0', paddingLeft: '16px' }}>
                        {entry.notes.map((note, j) => (
                          <li key={j} style={{ fontSize: '12px', color: '#6b7280', lineHeight: 1.5 }}>{note}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          <p style={{ textAlign: 'center', fontSize: '11px', color: '#d1d5db', marginTop: '32px', fontFamily: 'var(--mono)' }}>
            TimeBack AI · Pipeline #1 · {new Date().getFullYear()}
          </p>
        </div>
      </div>
    </>
  );
}
