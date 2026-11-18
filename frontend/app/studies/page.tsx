"use client";

/**
 * The work.
 *
 * A study is listed by what it *found*, not by a status chip — the headline is
 * the reason anyone opens it. Studies still running say so plainly with their
 * progress; there is no spinner pretending to be a result.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  ACTIVE_STUDY_STATUSES,
  personas as personaApi,
  studies,
  type PersonaEntry,
  type StudySummary,
} from "@/lib/api";

interface SegmentRow {
  audience: string;
  count: number;
  /** Empty means "synthesise a persona from the audience text". */
  personaId: string;
}

const EMPTY: SegmentRow[] = [
  { audience: "", count: 3, personaId: "" },
  { audience: "", count: 3, personaId: "" },
];

export default function StudiesPage() {
  const [list, setList] = useState<StudySummary[]>([]);
  const [productUrl, setProductUrl] = useState("");
  const [segments, setSegments] = useState<SegmentRow[]>(EMPTY);
  const [task, setTask] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [catalogue, setCatalogue] = useState<PersonaEntry[]>([]);

  const load = async () => {
    try {
      setList(await studies.list());
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => {
    load();
    // Offering the catalogue is what lets a segment skip synthesis entirely —
    // which also sidesteps the rate limit that blocks novel audiences.
    personaApi
      .list()
      .then((r) => setCatalogue(r.entries))
      .catch(() => setCatalogue([]));
  }, []);

  useEffect(() => {
    if (!list.some((s) => ACTIVE_STUDY_STATUSES.includes(s.status))) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [list]);

  const total = segments.reduce(
    (n, s) => n + (s.audience.trim() || s.personaId ? s.count : 0),
    0,
  );

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const study = await studies.create({
        product_url: productUrl.trim(),
        segments: segments
          .filter((s) => s.audience.trim() || s.personaId)
          .map((s) => ({
            // A chosen persona still needs an audience label for the report's
            // segment headings; its own label is the honest default.
            audience:
              s.audience.trim() ||
              catalogue.find((c) => c.persona.id === s.personaId)?.persona.label ||
              s.personaId,
            count: s.count,
            persona_id: s.personaId || null,
          })),
        task: task.trim() || null,
      });
      setProductUrl("");
      setSegments(EMPTY);
      setTask("");
      setList([study, ...list]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  function update(i: number, patch: Partial<SegmentRow>) {
    setSegments(segments.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  }

  return (
    <div className="dashboard-page">
      {/* ---- compose ------------------------------------------------------ */}
      <section className="dashboard-hero">
        <div>
          <span className="eyebrow">Research studio</span>
          <h1>Meet your customers<br /><em>before you build.</em></h1>
          <p>
            Put your product in front of AI customers and learn what they notice,
            where they hesitate, and what would make them come back.
          </p>
        </div>
        <div className="hero-orbit" aria-hidden="true">
          <span className="orbit-dot orbit-dot-one" />
          <span className="orbit-dot orbit-dot-two" />
          <span className="orbit-dot orbit-dot-three" />
          <div className="hero-orbit-core">✦</div>
        </div>
      </section>

      <section className="study-composer">
        <div className="composer-heading">
          <div>
            <span className="eyebrow">New study</span>
            <h2>Start with a product and an audience</h2>
          </div>
          <span className="composer-step">01 / 01</span>
        </div>
        <form onSubmit={submit}>
          <div className="field-block product-field">
            <label htmlFor="product-url">What should customers explore?</label>
            <input
              id="product-url"
              type="url"
              required
              value={productUrl}
              onChange={(e) => setProductUrl(e.target.value)}
              placeholder="https://your-product.com"
            />
          </div>

          <fieldset className="audience-fieldset">
            <legend>Who should we simulate?</legend>
            <p className="field-hint">Add one or more groups. Every group gets its own consistent customer persona.</p>
            <div className="audience-list">
              {segments.map((s, i) => (
                <div key={i} className="audience-card">
                  <span className="audience-number">{String(i + 1).padStart(2, "0")}</span>
                  <div className="segment-row">
                    <input
                      aria-label={`Audience ${i + 1}`}
                      value={s.audience}
                      onChange={(e) => update(i, { audience: e.target.value })}
                      placeholder={i === 0 ? "Startup founders" : "Marketing leaders"}
                      // Describing the audience is the default path; picking a
                      // persona below replaces it rather than the other way round.
                      disabled={Boolean(s.personaId)}
                      style={{ opacity: s.personaId ? 0.45 : 1 }}
                    />
                    <select
                      aria-label={`Persona for audience ${i + 1}`}
                      value={s.personaId}
                      onChange={(e) => update(i, { personaId: e.target.value })}
                    >
                      <option value="">Write one for me</option>
                      {catalogue.map((c) => (
                        <option key={c.persona.id} value={c.persona.id}>
                          {c.persona.label}
                          {c.origin === "custom" ? " ·" : ""}
                        </option>
                      ))}
                    </select>
                    <input
                      aria-label={`Customers in audience ${i + 1}`}
                      type="number"
                      min={1}
                      max={15}
                      value={s.count}
                      onChange={(e) => update(i, { count: Number(e.target.value) })}
                    />
                  </div>
                  <p className="audience-note">
                    {s.personaId
                      ? "Using this persona exactly — no model call, no waiting."
                      : "A persona will be written for this audience and reused next time."}
                  </p>
                </div>
              ))}
            </div>
            <button type="button" className="add-audience" onClick={() =>
              setSegments([...segments, { audience: "", count: 3, personaId: "" }])
            }>+ Add another audience</button>
          </fieldset>

          <div className="field-block task-field">
            <label htmlFor="task">Give them a task <span>Optional</span></label>
            <input
              id="task"
              value={task}
              onChange={(e) => setTask(e.target.value)}
              placeholder="Leave empty and each customer decides what to do"
            />
          </div>

          <div className="composer-actions">
            <button className="btn btn-primary" disabled={submitting || total === 0}>
              <span>{submitting ? "Starting study…" : `Launch ${total} customer${total === 1 ? "" : "s"}`}</span> <span aria-hidden="true">→</span>
            </button>
            <span className="run-note">Usually takes 5–10 minutes</span>
            {error ? (
              <span className="form-error">
                {error}
              </span>
            ) : null}
          </div>
        </form>
      </section>

      {/* ---- the work ----------------------------------------------------- */}
      <section className="studies-section">
        <div className="section-heading">
          <div><span className="eyebrow">Your research</span><h2>Recent studies</h2></div>
          <span className="study-count">{list.length} {list.length === 1 ? "study" : "studies"}</span>
        </div>

        {list.length === 0 ? (
          <div className="empty-state"><span>✦</span><p>No studies yet. Your first customer insight is one launch away.</p></div>
        ) : (
          <ul className="study-grid">
            {list.map((s) => {
              const active = ACTIVE_STUDY_STATUSES.includes(s.status);
              const done = (s.progress.completed ?? 0) + (s.progress.failed ?? 0);
              return (
                <li key={s.id}>
                  <Link href={`/studies/${s.id}`} className="study-card">
                    <div className="study-card-top">
                      <span className="study-domain">
                        {(() => {
                          try {
                            return new URL(s.product_url).hostname;
                          } catch {
                            return s.product_url;
                          }
                        })()}
                      </span>
                      <span className={`status-pill ${active ? "is-active" : ""}`}>
                        {active
                          ? `${done} of ${s.progress.total ?? "?"} done`
                          : s.status}
                      </span>
                    </div>

                    <p className="study-name">{s.name}</p>
                    <p className="study-headline">
                      {s.headline ??
                        (active
                          ? "Customers are still exploring…"
                          : "No findings were produced.")}
                    </p>
                    <div className="study-card-footer"><span>{s.segments.length} audience{s.segments.length === 1 ? "" : "s"}</span><span aria-hidden="true">↗</span></div>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
