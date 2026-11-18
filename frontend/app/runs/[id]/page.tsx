"use client";

/**
 * One customer's session.
 *
 * The report is what the evaluator concluded; the transcript below it is the
 * evidence that conclusion rests on. Keeping them on one page, in that order,
 * is the point — a claim you cannot walk back to the moment it came from is
 * exactly what this product is trying not to produce.
 */

import Link from "next/link";
import { use, useEffect, useState } from "react";
import {
  ACTIVE_STATUSES,
  api,
  formatDuration,
  type RunDetail,
  type SessionEvent,
} from "@/lib/api";
import { Transcript } from "@/components/transcript";
import { ClaimTag, Quote, SevTag, StatTile, isSolid } from "@/components/report";

export default function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const [detail, ev] = await Promise.all([api.getRun(id), api.getEvents(id)]);
      setRun(detail);
      setEvents(ev.events);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => {
    load();
  }, [id]);

  useEffect(() => {
    if (!run || !ACTIVE_STATUSES.includes(run.status)) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [run?.status]);

  if (error)
    return (
      <p className="measure" style={{ color: "var(--sev-critical)" }}>
        {error}
      </p>
    );
  if (!run)
    return (
      <p className="eyebrow" style={{ paddingBlock: "var(--s-16)" }}>
        Loading…
      </p>
    );

  const active = ACTIVE_STATUSES.includes(run.status);
  const report = run.report;

  // The backend returns a placeholder report when evaluation itself fails, so
  // the session stays inspectable. Its fields are literals — "none", 3, false —
  // and rendering them as a verdict would state things no one concluded. The
  // sentinel is set only by that path.
  const unanalysed = report?.first_impression === "(not analysed)";

  const act = (fn: () => Promise<unknown>) => {
    setBusy(true);
    fn()
      .then(load)
      .catch((e) => setError((e as Error).message))
      .finally(() => setBusy(false));
  };

  return (
    <article style={{ paddingBottom: "var(--s-24)" }}>
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
          <span className="eyebrow">{run.status}</span>
          <span className="eyebrow" style={{ color: "var(--ink-muted)" }}>
            {run.mode}
          </span>
          <span className="eyebrow" style={{ color: "var(--ink-muted)" }}>
            {new URL(run.product_url).hostname}
          </span>
          <div style={{ marginLeft: "auto", display: "flex", gap: "var(--s-6)" }}>
            {active ? (
              <button
                className="btn-quiet"
                disabled={busy}
                onClick={() => act(() => api.cancelRun(id))}
              >
                Stop session
              </button>
            ) : null}
            {!active ? (
              <button
                className="btn-quiet"
                disabled={busy}
                onClick={() => act(() => api.reevaluateRun(id))}
              >
                {busy ? "Re-evaluating…" : "Re-evaluate"}
              </button>
            ) : null}
          </div>
        </div>

        <h1 style={{ marginTop: "var(--s-8)", maxWidth: "42rem" }}>
          {run.persona_label}
        </h1>
        <p
          className="measure"
          style={{ marginTop: "var(--s-3)", color: "var(--ink-secondary)" }}
        >
          Simulating <strong style={{ color: "var(--ink)" }}>{run.audience}</strong>
          {run.task ? (
            <>
              {" "}
              with the task &ldquo;{run.task}&rdquo;
            </>
          ) : (
            " with no task — the customer chose what to do"
          )}
          .
        </p>

        <p
          className="eyebrow"
          style={{ marginTop: "var(--s-4)", textTransform: "none", letterSpacing: 0 }}
        >
          {run.event_count} events · {formatDuration(run.duration_ms)} ·{" "}
          {run.model}
          {run.persona_source !== "catalogue"
            ? ` · persona ${run.persona_source}`
            : ""}
        </p>

        {run.error ? (
          <p
            className="sunk measure"
            style={{ marginTop: "var(--s-6)", borderLeft: "2px solid var(--sev-critical)" }}
          >
            {run.error}
          </p>
        ) : null}
      </header>

      {unanalysed ? (
        <>
          <hr className="rule-strong" />
          <section style={{ paddingBlock: "var(--s-8)" }}>
            <span className="eyebrow">Not analysed</span>
            <h2
              className="display measure"
              style={{ fontSize: "var(--t-h3)", marginTop: "var(--s-2)" }}
            >
              The session was recorded, but the evaluation step did not run.
            </h2>
            <p
              className="measure"
              style={{ marginTop: "var(--s-4)", color: "var(--ink-secondary)" }}
            >
              Every event and screenshot below is intact — only the interpretation
              is missing. Re-evaluating reuses this stored session and costs no
              browser time.
            </p>
            {report?.abandonment_reason ? (
              <p
                className="sunk"
                style={{
                  marginTop: "var(--s-6)",
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--t-micro)",
                  color: "var(--ink-secondary)",
                  overflowWrap: "anywhere",
                }}
              >
                {report.abandonment_reason}
              </p>
            ) : null}
          </section>
        </>
      ) : report ? (
        <>
          <hr className="rule-strong" />
          <section style={{ paddingBlock: "var(--s-8)" }}>
            <h2 className="display" style={{ maxWidth: "42rem", fontSize: "var(--t-h3)" }}>
              {report.summary}
            </h2>

            <div
              style={{
                display: "grid",
                gap: "var(--s-8)",
                gridTemplateColumns: "repeat(auto-fit, minmax(9rem, 1fr))",
                marginTop: "var(--s-8)",
              }}
            >
              <StatTile
                value={report.would_continue ? "Yes" : "No"}
                label="Would continue"
                note={report.outcome ?? undefined}
              />
              <StatTile
                value={`${report.customer_satisfaction}`}
                label="Satisfaction"
                note="out of 5"
              />
              <StatTile value={report.purchase_intent} label="Purchase intent" />
              <StatTile value={report.retention_intent} label="Retention intent" />
            </div>

            <div style={{ marginTop: "var(--s-8)", display: "grid", gap: "var(--s-6)" }}>
              <div>
                <span className="eyebrow">They thought it was</span>
                <p className="measure" style={{ color: "var(--ink-secondary)" }}>
                  {report.what_they_thought_it_was}
                </p>
              </div>
              <div>
                <span className="eyebrow">First impression</span>
                <p className="measure" style={{ color: "var(--ink-secondary)" }}>
                  {report.first_impression}
                </p>
              </div>
              {report.abandonment_reason ? (
                <div>
                  <span className="eyebrow">Why they left</span>
                  <p className="measure" style={{ color: "var(--ink-secondary)" }}>
                    {report.abandonment_reason}
                  </p>
                </div>
              ) : null}
            </div>
          </section>

          {report.findings.length > 0 ? (
            <>
              <hr className="rule-strong" />
              <section style={{ paddingBlock: "var(--s-8)" }}>
                <h2 className="eyebrow">Findings</h2>
                {report.findings.map((f, i) => (
                  <div
                    key={i}
                    className={isSolid(f.claim_type) ? "claim-observed" : "claim-soft"}
                    style={{
                      paddingBlock: "var(--s-6)",
                      borderTop: i > 0 ? "1px solid var(--rule)" : undefined,
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        gap: "var(--s-4)",
                        alignItems: "baseline",
                        flexWrap: "wrap",
                        marginBottom: "var(--s-2)",
                      }}
                    >
                      <SevTag severity={f.severity} />
                      <ClaimTag claim={f.claim_type} />
                      <span
                        className="eyebrow"
                        style={{ textTransform: "none", letterSpacing: 0 }}
                      >
                        {f.dimension}
                      </span>
                      {/* An observation must be traceable to a moment in the session. */}
                      {f.evidence.length > 0 ? (
                        <span
                          className="eyebrow"
                          style={{ marginLeft: "auto", color: "var(--ink-muted)" }}
                        >
                          steps {f.evidence.map((e) => e.event_seq).join(", ")}
                        </span>
                      ) : null}
                    </div>
                    <p className="claim-statement measure" style={{ color: "var(--ink)" }}>
                      {f.observation}
                    </p>
                    <p
                      className="measure"
                      style={{
                        color: "var(--ink-secondary)",
                        fontSize: "var(--t-small)",
                        marginTop: "var(--s-2)",
                      }}
                    >
                      {f.impact}
                    </p>
                  </div>
                ))}
              </section>
            </>
          ) : null}

          {report.quotes.length > 0 ? (
            <>
              <hr className="rule-strong" />
              <section style={{ paddingBlock: "var(--s-8)" }}>
                <h2 className="eyebrow">In their words</h2>
                <div style={{ display: "grid", gap: "var(--s-6)", marginTop: "var(--s-4)" }}>
                  {report.quotes.map((q, i) => (
                    <Quote key={i}>{q}</Quote>
                  ))}
                </div>
              </section>
            </>
          ) : null}
        </>
      ) : null}

      <hr className="rule-strong" />
      <section style={{ paddingTop: "var(--s-8)" }}>
        <h2 className="eyebrow">Session transcript</h2>
        <p
          className="measure"
          style={{
            marginTop: "var(--s-2)",
            marginBottom: "var(--s-4)",
            fontSize: "var(--t-small)",
            color: "var(--ink-muted)",
          }}
        >
          Everything the customer did, and everything they said while doing it.
        </p>
        <Transcript events={events} />
      </section>
    </article>
  );
}
