"use client";

/**
 * The session, read as a transcript.
 *
 * A session is mostly mechanical — navigate, snapshot, click, snapshot — but the
 * only part a human wants to read is what the customer *thought*. So the notes
 * are set as the voice of the piece and everything else recedes to a thin
 * marginal rail: present, timestamped, checkable, but never competing with the
 * reaction it produced.
 *
 * Screenshots are plates, not thumbnails — a figure with a caption naming the
 * step, expanding to full width on click.
 */

import { useState } from "react";
import type { SessionEvent } from "@/lib/api";

const NOTE_TYPES = new Set(["note", "reflection"]);

/** The quiet rail: what the agent did, in the margin. */
function Action({ event }: { event: SessionEvent }) {
  const label =
    event.action ??
    event.type.replace(/_/g, " ") ??
    "step";
  const where = event.url ? new URL(event.url).pathname : null;

  return (
    <div
      style={{
        display: "flex",
        gap: "var(--s-3)",
        alignItems: "baseline",
        paddingBlock: "var(--s-2)",
        fontSize: "var(--t-small)",
        color: "var(--ink-muted)",
      }}
    >
      <span
        className="eyebrow"
        style={{ minWidth: "2.5rem", fontVariantNumeric: "tabular-nums" }}
      >
        {event.seq}
      </span>
      <span style={{ minWidth: "6rem" }}>{label}</span>
      {event.target ? (
        <span style={{ color: "var(--ink-secondary)" }}>{event.target}</span>
      ) : null}
      {where && where !== "/" ? (
        <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--t-micro)" }}>
          {where}
        </span>
      ) : null}
      {event.error ? (
        <span className="sev-tag sev-critical" style={{ marginLeft: "auto" }}>
          error
        </span>
      ) : null}
    </div>
  );
}

function Plate({ event }: { event: SessionEvent }) {
  const [open, setOpen] = useState(false);
  if (!event.screenshot_url) return null;
  return (
    <figure style={{ margin: 0, paddingBlock: "var(--s-4)" }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        className="plate"
        src={event.screenshot_url}
        alt={`What the customer saw at step ${event.seq}${
          event.url ? ` on ${event.url}` : ""
        }`}
        loading="lazy"
        onClick={() => setOpen((v) => !v)}
        style={{
          maxWidth: open ? "100%" : "min(100%, 30rem)",
          cursor: open ? "zoom-out" : "zoom-in",
        }}
      />
      <figcaption className="plate-caption">
        Step {event.seq}
        {event.url ? ` · ${event.url}` : ""} — click to {open ? "shrink" : "enlarge"}
      </figcaption>
    </figure>
  );
}

export function Transcript({ events }: { events: SessionEvent[] }) {
  if (events.length === 0)
    return (
      <p className="measure" style={{ color: "var(--ink-secondary)" }}>
        No events were recorded for this session.
      </p>
    );

  return (
    <div>
      {events.map((e) => {
        const isNote = NOTE_TYPES.has(e.type) && e.observation;
        if (isNote) {
          return (
            <div key={e.seq} style={{ paddingBlock: "var(--s-6)" }}>
              <blockquote className="pullquote">{e.observation}</blockquote>
            </div>
          );
        }
        return (
          <div key={e.seq}>
            <Action event={e} />
            <Plate event={e} />
          </div>
        );
      })}
    </div>
  );
}
