/**
 * Report primitives.
 *
 * Two rules run through all of these:
 *
 * 1. Severity is a *status* colour, so it never travels alone — every severity
 *    mark ships with its word. On the light surface two of the steps are
 *    deliberately below 3:1, and the label is the mitigation, not decoration.
 *
 * 2. Claim type is rendered typographically, not just labelled. An observed
 *    behaviour is set solid and cites an event; an interpretation or hypothesis
 *    is set lighter and italic so it *looks* provisional at a glance. The
 *    epistemic contract is the product's core promise, so it has to survive
 *    someone skimming.
 */
import type { ClaimType, ConsensusFinding, Severity } from "@/lib/api";

const SEVERITY_CLASS: Record<Severity, string> = {
  critical: "sev-critical",
  high: "sev-high",
  medium: "sev-medium",
  low: "sev-low",
  positive: "sev-positive",
};

const CLAIM_LABEL: Record<ClaimType, string> = {
  observed_behavior: "Observed",
  interpretation: "Interpretation",
  hypothesis: "Hypothesis",
};

export const isSolid = (c: ClaimType) => c === "observed_behavior";

export function SevTag({ severity }: { severity: Severity }) {
  return (
    <span className={`sev-tag ${SEVERITY_CLASS[severity]}`}>{severity}</span>
  );
}

export function ClaimTag({ claim }: { claim: ClaimType }) {
  const solid = isSolid(claim);
  return (
    <span className={`claim-tag${solid ? "" : " is-soft"}`}>
      {CLAIM_LABEL[claim]}
    </span>
  );
}

/** "3 of 4" — the count is the point, so the number is set larger than the bar. */
export function AgreementBar({
  hit,
  total,
  severity = "low",
  label,
}: {
  hit: number;
  total: number;
  severity?: Severity;
  label?: string;
}) {
  const pct = total > 0 ? (hit / total) * 100 : 0;
  return (
    <div className={SEVERITY_CLASS[severity]}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: "var(--s-4)",
          marginBottom: "var(--s-2)",
        }}
      >
        {label ? (
          <span style={{ fontSize: "var(--t-small)", color: "var(--ink-secondary)" }}>
            {label}
          </span>
        ) : null}
        <span style={{ fontSize: "var(--t-small)", color: "var(--ink-secondary)" }}>
          <strong style={{ color: "var(--ink)", fontWeight: 600 }}>{hit}</strong> of {total}
        </span>
      </div>
      <div
        className="bar-track"
        role="img"
        aria-label={`${hit} of ${total} customers${label ? ` in ${label}` : ""}`}
      >
        <div className="bar-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function StatTile({
  value,
  label,
  note,
}: {
  value: string;
  label: string;
  note?: string;
}) {
  return (
    <div>
      <div className="stat-value">{value}</div>
      <div className="stat-label" style={{ marginTop: "var(--s-2)" }}>
        {label}
      </div>
      {note ? (
        <div
          style={{
            fontSize: "var(--t-small)",
            color: "var(--ink-muted)",
            marginTop: "var(--s-1)",
          }}
        >
          {note}
        </div>
      ) : null}
    </div>
  );
}

/** The customer's own words are the evidence, so they get real pull-quote treatment. */
export function Quote({ children }: { children: React.ReactNode }) {
  return <blockquote className="pullquote">&ldquo;{children}&rdquo;</blockquote>;
}

export function Finding({
  finding,
  rank,
  demoted = false,
}: {
  finding: ConsensusFinding;
  rank?: number;
  demoted?: boolean;
}) {
  const solid = isSolid(finding.claim_type);
  const hitSegments = finding.segments.filter((s) => s.hit > 0);
  return (
    <article
      className={`spread ${solid ? "claim-observed" : "claim-soft"}`}
      style={{ paddingBlock: "var(--s-8)", opacity: demoted ? 0.82 : 1 }}
    >
      {/* The apparatus lives in the margin, so it can be scanned without
          interrupting the sentence being read. */}
      <div className="rail">
        <div style={{ display: "flex", gap: "var(--s-3)", alignItems: "baseline" }}>
          {rank ? (
            <span className="eyebrow" style={{ color: "var(--ink-muted)" }}>
              {String(rank).padStart(2, "0")}
            </span>
          ) : null}
          <SevTag severity={finding.severity} />
        </div>
        <div style={{ display: "grid", gap: "var(--s-1)" }}>
          <ClaimTag claim={finding.claim_type} />
          <span className="rail-quiet">{finding.dimension}</span>
        </div>

        {/* The pooled count is only meaningful when a finding crosses segments.
            On a segment-specific one it actively misleads: a finding unanimous
            among recruiters renders as "2 of 4" — 50%, indistinguishable from a
            split verdict — because the founders who never saw it are counted as
            disagreeing. When one segment owns a finding, its own bar is both
            more accurate and more alarming. */}
        {hitSegments.length > 1 ? (
          <AgreementBar
            hit={finding.agreement_count}
            total={finding.run_count}
            severity={finding.severity}
            label="All customers"
          />
        ) : null}
        {/* Only segments that actually hit get a bar; a row of zeroes is noise,
            and the matrix already carries the full grid. */}
        {hitSegments.map((s) => (
          <AgreementBar
            key={s.segment}
            hit={s.hit}
            total={s.total}
            severity={finding.severity}
            label={s.segment}
          />
        ))}
      </div>

      <div>
        <h3
          className="claim-statement"
          style={{ marginBottom: "var(--s-4)", color: "var(--ink)", maxWidth: "40rem" }}
        >
          {finding.statement}
        </h3>

        <p className="measure" style={{ color: "var(--ink-secondary)" }}>
          {finding.impact}
        </p>

        {finding.quotes.slice(0, 2).map((q, i) => (
          <div key={i} style={{ marginTop: "var(--s-6)" }}>
            <Quote>{q}</Quote>
          </div>
        ))}

        {finding.recommendation ? (
          <p
            className="measure"
            style={{
              marginTop: "var(--s-6)",
              paddingTop: "var(--s-4)",
              borderTop: "1px solid var(--rule)",
              fontSize: "var(--t-small)",
              color: "var(--ink-secondary)",
            }}
          >
            <span className="eyebrow" style={{ marginRight: "var(--s-2)" }}>
              Recommendation
            </span>
            {finding.recommendation}
          </p>
        ) : null}
      </div>
    </article>
  );
}

/** Which audience hit what — the artifact only a study can produce. */
export function Matrix({ findings }: { findings: ConsensusFinding[] }) {
  const segments = Array.from(
    new Set(findings.flatMap((f) => f.segments.map((s) => s.segment))),
  );
  if (segments.length === 0 || findings.length === 0) return null;

  return (
    <div className="scroll-x">
      <table className="matrix" style={{ minWidth: "min(100%, 40rem)" }}>
        <caption className="eyebrow" style={{ textAlign: "left", marginBottom: "var(--s-4)" }}>
          Segment &times; finding
        </caption>
        <thead>
          <tr>
            <th scope="col" className="eyebrow" style={{ minWidth: "16rem" }}>
              Finding
            </th>
            {segments.map((s) => (
              <th key={s} scope="col" className="eyebrow">
                {s}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {findings.map((f, i) => (
            <tr key={i} className={SEVERITY_CLASS[f.severity]}>
              <th
                scope="row"
                style={{
                  fontWeight: 400,
                  fontSize: "var(--t-small)",
                  maxWidth: "22rem",
                  whiteSpace: "normal",
                }}
              >
                {f.dimension}
              </th>
              {segments.map((s) => {
                const hit = f.segments.find((x) => x.segment === s);
                if (!hit || hit.hit === 0) {
                  return (
                    <td key={s} className="cell" style={{ color: "var(--ink-muted)" }}>
                      &mdash;
                    </td>
                  );
                }
                // Intensity encodes proportion; the count is printed so the cell
                // never depends on colour alone to be read.
                const ratio = hit.total > 0 ? hit.hit / hit.total : 0;
                return (
                  <td
                    key={s}
                    className="cell"
                    style={{
                      background: `color-mix(in oklab, var(--sev) ${Math.round(
                        18 + ratio * 42,
                      )}%, var(--surface))`,
                    }}
                  >
                    {hit.hit} of {hit.total}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
