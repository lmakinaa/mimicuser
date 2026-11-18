"use client";

/**
 * The study report — the product's actual output.
 *
 * Ordered the way a reader needs it, which is not the order the API returns it:
 * the headline, then the verdict, then the findings ranked by weight, then the
 * segment matrix, then environment warnings (visually demoted — a blocked
 * browser must never read as a product defect), then the sessions behind it all.
 */

import Link from "next/link";
import { Suspense, use, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ACTIVE_STUDY_STATUSES,
  studies,
  type ConsensusFinding,
  type Severity,
  type StudyDetail,
  type StudyReport,
} from "@/lib/api";
import { Finding, Matrix, StatTile } from "@/components/report";
import { PREVIEW_NOTICE, PREVIEW_REPORT } from "@/lib/fixture";

/** Severity first, then how many customers agreed. */
const WEIGHT: Record<Severity, number> = {
  critical: 5,
  high: 4,
  medium: 3,
  positive: 2,
  low: 1,
};

const rank = (fs: ConsensusFinding[]) =>
  [...fs].sort(
    (a, b) =>
      WEIGHT[b.severity] - WEIGHT[a.severity] ||
      b.agreement_count / Math.max(b.run_count, 1) -
        a.agreement_count / Math.max(a.run_count, 1),
  );

export default function StudyPage({ params }: { params: Promise<{ id: string }> }) {
  // useSearchParams suspends, so the boundary has to exist above it.
  return (
    <Suspense fallback={<p className="eyebrow" style={{ paddingBlock: "var(--s-16)" }}>Loading…</p>}>
      <StudyReport params={params} />
    </Suspense>
  );
}

function StudyReport({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const search = useSearchParams();
  const router = useRouter();
  const [study, setStudy] = useState<StudyDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [restarting, setRestarting] = useState(false);

  const load = async () => {
    try {
      setStudy(await studies.get(id));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => {
    load();
  }, [id]);

  useEffect(() => {
    if (!study || !ACTIVE_STUDY_STATUSES.includes(study.status)) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [study?.status]);

  const live = study?.report ?? null;
  // The fixture is opt-in and only ever stands in for a report with nothing in
  // it, so a real result can never be silently replaced by a reconstruction.
  const preview = search.get("preview") === "1" && !live?.consensus_findings?.length;
  const report: StudyReport | null = preview ? PREVIEW_REPORT : live;

  const findings = useMemo(() => rank(report?.consensus_findings ?? []), [report]);

  if (error)
    return (
      <p className="measure" style={{ color: "var(--sev-critical)" }}>
        {error}
      </p>
    );
  if (!study)
    return (
      <p className="eyebrow" style={{ paddingBlock: "var(--s-16)" }}>
        Loading…
      </p>
    );

  const active = ACTIVE_STUDY_STATUSES.includes(study.status);
  const done = (study.progress.completed ?? 0) + (study.progress.failed ?? 0);
  const pct = (v: number) => `${Math.round(v * 100)}%`;

  async function restartStudy() {
    if (!study) return;
    const currentStudy = study;
    setRestarting(true);
    setError(null);
    try {
      const restarted = await studies.create({
        product_url: currentStudy.product_url,
        name: currentStudy.name,
        project_id: currentStudy.project_id,
        task: currentStudy.task,
        budget: currentStudy.budget as {
          max_tool_calls: number;
          max_wall_seconds: number;
          max_cost_usd: number;
        },
        segments: currentStudy.segments.map((segment) => ({
          audience: String(segment.audience),
          count: Number(segment.count),
          persona_id: segment.persona_id ? String(segment.persona_id) : null,
        })),
      });
      router.push(`/studies/${restarted.id}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRestarting(false);
    }
  }

  return (
    <article style={{ paddingBottom: "var(--s-24)" }}>
      {/* ---- masthead ---------------------------------------------------- */}
      <header style={{ paddingBlock: "var(--s-8) var(--s-12)" }}>
        <Link href="/studies" className="eyebrow" style={{ textDecoration: "none" }}>
          ← All studies
        </Link>

        <div
          style={{
            display: "flex",
            gap: "var(--s-4)",
            alignItems: "baseline",
            flexWrap: "wrap",
            marginTop: "var(--s-8)",
          }}
        >
          <span className="eyebrow">{study.name}</span>
          <span className="eyebrow" style={{ color: "var(--ink-muted)" }}>
            {new URL(study.product_url).hostname}
          </span>
          <span className="eyebrow">{study.status}</span>
          {active ? (
            <button
              className="btn-quiet"
              style={{ marginLeft: "auto" }}
              onClick={() =>
                studies.cancel(id).then(load).catch((e) => setError(e.message))
              }
            >
              Stop study
            </button>
          ) : (
            <button
              className="btn-quiet"
              style={{ marginLeft: "auto" }}
              disabled={restarting}
              onClick={restartStudy}
            >
              {restarting ? "Restarting…" : "Restart study"}
            </button>
          )}
        </div>

        {preview ? (
          <p
            className="sunk"
            style={{
              marginTop: "var(--s-6)",
              fontSize: "var(--t-small)",
              color: "var(--ink-secondary)",
              borderLeft: "2px solid var(--sev-medium)",
            }}
          >
            <strong style={{ color: "var(--ink)" }}>Preview — not live output.</strong>{" "}
            {PREVIEW_NOTICE}
          </p>
        ) : null}

        {report ? (
          <h1 className="display" style={{ marginTop: "var(--s-8)", maxWidth: "46rem" }}>
            {report.headline}
          </h1>
        ) : (
          <h1 className="display" style={{ marginTop: "var(--s-8)", maxWidth: "46rem" }}>
            {active
              ? `Running — ${done} of ${study.progress.total ?? study.runs.length} customers finished.`
              : "This study produced no findings."}
          </h1>
        )}

        {!report && !active ? (
          <p
            className="measure"
            style={{ marginTop: "var(--s-4)", color: "var(--ink-secondary)" }}
          >
            The sessions were recorded, but the evaluation step did not complete —
            usually a rate limit rather than a lack of material. Re-running the
            evaluation reuses the stored sessions and costs no browser time.
          </p>
        ) : null}
      </header>

      {report ? (
        <>
          {/* ---- verdict ------------------------------------------------- */}
          <hr className="rule-strong" />
          <section
            style={{
              display: "grid",
              gap: "var(--s-8)",
              gridTemplateColumns: "repeat(auto-fit, minmax(9rem, 1fr))",
              paddingBlock: "var(--s-8)",
            }}
          >
            <StatTile
              value={pct(report.would_continue_rate)}
              label="Would continue"
              note={`${report.analysed_count} of ${report.run_count} analysed`}
            />
            {report.segments.map((s) => (
              <StatTile
                key={s.segment}
                value={
                  s.mean_satisfaction != null ? s.mean_satisfaction.toFixed(1) : "—"
                }
                label="Satisfaction"
                note={`${s.persona_label} · out of 5`}
              />
            ))}
            {/* A distribution, not four separate statistics — rendering each
                bucket as its own tile made "2 would consider / 2 unlikely" read
                as two unrelated numbers. */}
            <div>
              <div className="stat-label">Purchase intent</div>
              <dl
                style={{
                  display: "grid",
                  gap: "var(--s-1)",
                  margin: "var(--s-3) 0 0",
                }}
              >
                {Object.entries(report.purchase_intent).map(([k, v]) => (
                  <div
                    key={k}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      gap: "var(--s-3)",
                      fontSize: "var(--t-small)",
                    }}
                  >
                    <dt style={{ color: "var(--ink-secondary)" }}>
                      {k.replace(/_/g, " ")}
                    </dt>
                    <dd style={{ margin: 0, fontWeight: 600 }}>{v}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </section>

          {/* ---- findings ------------------------------------------------ */}
          <hr className="rule-strong" />
          <section style={{ paddingTop: "var(--s-8)" }}>
            <h2 className="eyebrow">What customers ran into</h2>
            <div style={{ marginTop: "var(--s-4)" }}>
              {findings.length === 0 ? (
                <p className="measure" style={{ color: "var(--ink-secondary)" }}>
                  No consensus findings — nothing was reported by enough customers
                  to be worth acting on.
                </p>
              ) : (
                findings.map((f, i) => (
                  <div key={i}>
                    {i > 0 ? <hr className="rule" /> : null}
                    <Finding finding={f} rank={i + 1} />
                  </div>
                ))
              )}
            </div>
          </section>

          {/* ---- matrix -------------------------------------------------- */}
          {findings.length > 0 ? (
            <>
              <hr className="rule-strong" />
              <section style={{ paddingBlock: "var(--s-8)" }}>
                <Matrix findings={findings} />
              </section>
            </>
          ) : null}

          {/* ---- environment: demoted, and separated on purpose ---------- */}
          {report.environment_warnings.length > 0 ? (
            <>
              <hr className="rule-strong" />
              <section style={{ paddingBlock: "var(--s-8)" }}>
                <h2 className="eyebrow">Possibly the harness, not the product</h2>
                <p
                  className="measure"
                  style={{
                    marginTop: "var(--s-2)",
                    fontSize: "var(--t-small)",
                    color: "var(--ink-muted)",
                  }}
                >
                  Separated because an automated browser gets blocked in ways a real
                  customer would not. Confirm by hand before treating any of these as
                  a defect.
                </p>
                {report.environment_warnings.map((f, i) => (
                  <div key={i}>
                    {i > 0 ? <hr className="rule" /> : null}
                    <Finding finding={f} demoted />
                  </div>
                ))}
              </section>
            </>
          ) : null}
        </>
      ) : null}

      {/* ---- sessions ---------------------------------------------------- */}
      <hr className="rule-strong" />
      <section style={{ paddingTop: "var(--s-8)" }}>
        <h2 className="eyebrow">Sessions</h2>
        <ul style={{ listStyle: "none", padding: 0, marginTop: "var(--s-4)" }}>
          {study.runs.map((r) => (
            <li key={r.id} style={{ borderTop: "1px solid var(--rule)" }}>
              <Link
                href={`/runs/${r.id}`}
                style={{
                  display: "flex",
                  gap: "var(--s-4)",
                  alignItems: "baseline",
                  flexWrap: "wrap",
                  paddingBlock: "var(--s-4)",
                  textDecoration: "none",
                }}
              >
                <span style={{ fontWeight: 600 }}>{r.persona_label}</span>
                <span className="eyebrow">{r.segment}</span>
                <span
                  style={{
                    color: "var(--ink-secondary)",
                    fontSize: "var(--t-small)",
                    marginLeft: "auto",
                  }}
                >
                  {r.outcome ?? r.status}
                </span>
                <span
                  className="eyebrow"
                  style={{
                    minWidth: "5.5rem",
                    textAlign: "right",
                    color:
                      r.would_continue == null
                        ? "var(--ink-muted)"
                        : r.would_continue
                          ? "var(--sev-positive)"
                          : "var(--sev-high)",
                  }}
                >
                  {r.would_continue == null
                    ? "—"
                    : r.would_continue
                      ? "would continue"
                      : "dropped off"}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </article>
  );
}
