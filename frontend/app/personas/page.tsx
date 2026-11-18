"use client";

/**
 * The catalogue.
 *
 * These are simulation parameters, not measurements, and the page says so —
 * a trait level here is a dial the model is told to behave as, not a finding
 * about real people. Presenting them as data would misrepresent what they are.
 */

import { useEffect, useState } from "react";
import { api, type Persona } from "@/lib/api";

export default function PersonasPage() {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listPersonas()
      .then((r) => setPersonas(r.personas))
      .catch((e) => setError((e as Error).message));
  }, []);

  if (error)
    return (
      <p className="measure" style={{ color: "var(--sev-critical)" }}>
        {error}
      </p>
    );

  return (
    <div style={{ paddingBottom: "var(--s-24)" }}>
      <header style={{ paddingBlock: "var(--s-12) var(--s-8)" }}>
        <h1 style={{ maxWidth: "26rem" }}>Who we can be.</h1>
        <p
          className="measure"
          style={{ marginTop: "var(--s-3)", color: "var(--ink-secondary)" }}
        >
          Name any audience when you start a study. If one of these covers it, it
          is adopted as written; if not, a persona is composed for that audience
          and reused for every later study naming it.
        </p>
        <p
          className="measure"
          style={{
            marginTop: "var(--s-4)",
            fontSize: "var(--t-small)",
            color: "var(--ink-muted)",
          }}
        >
          Trait levels are simulation parameters, not measurements. They set how
          a customer behaves in a session; they do not claim anything about real
          people in that audience.
        </p>
      </header>

      <hr className="rule-strong" />

      {personas.length === 0 ? (
        <p className="eyebrow" style={{ paddingBlock: "var(--s-8)" }}>
          Loading…
        </p>
      ) : (
        personas.map((p, i) => (
          <article
            key={p.id}
            className="spread"
            style={{
              paddingBlock: "var(--s-8)",
              borderTop: i > 0 ? "1px solid var(--rule)" : undefined,
            }}
          >
            <div className="rail">
              <span className="eyebrow">{p.label}</span>
              <span className="rail-quiet" style={{ fontFamily: "var(--font-mono)" }}>
                {p.id}
              </span>
              {p.aliases.length > 0 ? (
                <span className="rail-quiet">
                  also: {p.aliases.slice(0, 4).join(", ")}
                </span>
              ) : null}
            </div>

            <div>
              <p className="measure" style={{ fontSize: "var(--t-lead)" }}>
                {p.summary}
              </p>
              <dl
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(11rem, 1fr))",
                  gap: "var(--s-3) var(--s-6)",
                  margin: "var(--s-6) 0 0",
                }}
              >
                {Object.entries(p.traits).map(([k, v]) => (
                  <div key={k} style={{ borderTop: "1px solid var(--rule)", paddingTop: "var(--s-2)" }}>
                    <dt className="stat-label">{k.replace(/_/g, " ")}</dt>
                    <dd style={{ margin: 0, fontSize: "var(--t-small)" }}>
                      {typeof v === "string" ? v : JSON.stringify(v)}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          </article>
        ))
      )}
    </div>
  );
}
