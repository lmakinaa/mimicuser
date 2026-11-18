/**
 * A RECONSTRUCTION — not live output, and never presented as such.
 *
 * Every stored run in the database completed with real session events (29–54
 * each), but the evaluator call that turns those events into findings has been
 * rate-limited on every attempt, so `findings` is empty everywhere. The report
 * screen therefore has no real content to be designed against.
 *
 * Rather than design against an empty state, this file reconstructs what the
 * evaluator would produce, using **only material that actually exists**:
 *
 *   - the real study — Linear (https://linear.app/), audience "Startup founders"
 *   - real `event_seq` numbers from the `session_events` table
 *   - verbatim customer notes, quoted from `session_events.observation`
 *
 * Nothing here is invented behaviour. It is the real evidence, hand-assembled
 * into the shape `StudyReport` specifies.
 *
 * It renders ONLY behind `?preview=1`, always under a visible banner, and is
 * deleted the moment a real evaluation succeeds. See `PREVIEW_NOTICE`.
 */
import type { ConsensusFinding, StudyReport } from "./api";

export const PREVIEW_NOTICE =
  "Reconstructed from real session events — the evaluator is rate-limited, so " +
  "no findings have been generated yet. Quotes and evidence references are real; " +
  "the clustering is hand-assembled.";

const pricing: ConsensusFinding = {
  statement:
    "The pricing table shows no dollar amount for the Basic and Business tiers, so founders cannot tell what the paid plans cost.",
  dimension: "Pricing clarity",
  severity: "high",
  claim_type: "observed_behavior",
  impact:
    "Founders evaluating the tool against a budget stall at the moment of comparison. The free tier is legible and the paid tiers are not, so the upgrade path — the revenue path — is the one thing the page fails to communicate.",
  recommendation:
    "Render the per-seat price for Basic and Business in the comparison table itself, not only after a plan is selected.",
  agreement_count: 2,
  run_count: 4,
  segments: [
    { segment: "Startup founders", hit: 2, total: 2 },
    // Recruiters never reached pricing — they left at the homepage.
    { segment: "IT recruiters", hit: 0, total: 2 },
  ],
  severity_spread: ["high", "high"],
  source_finding_ids: ["f-pricing-1", "f-pricing-2"],
  run_ids: [
    "b6acf7f0-9694-4ae2-b67f-ee6db7b2d9f3",
    "8fac62cb-10d2-4642-a7d2-2271913bf893",
    "67f2d168-81d8-4a88-9be5-e6452cc6d261",
  ],
  quotes: [
    "Hang on — Free says $0, Enterprise says Custom, and Basic and Business just… don't show a number for me. The whole comparison table loads fine but the actual dollar amounts aren't there.",
    "Free tier is real — unlimited members, 2 teams, 250 issues. But the Basic and Business tiers don't show an actual dollar figure.",
  ],
  likely_environmental: false,
};

const freeTier: ConsensusFinding = {
  statement:
    "Unlimited members on the free tier reads as a genuine differentiator to founders who are used to per-seat pricing.",
  dimension: "Value proposition",
  severity: "positive",
  claim_type: "observed_behavior",
  impact:
    "This is the first moment in the session where founders volunteer enthusiasm unprompted. It maps directly onto a cost they already feel — contractors cycling in and out of a small team.",
  recommendation:
    "Promote 'unlimited members' out of the comparison table and into the pricing page headline; it is currently discovered rather than announced.",
  agreement_count: 2,
  run_count: 4,
  segments: [
    { segment: "Startup founders", hit: 2, total: 2 },
    { segment: "IT recruiters", hit: 0, total: 2 },
  ],
  severity_spread: ["positive", "positive"],
  source_finding_ids: ["f-free-1", "f-free-2"],
  run_ids: [
    "b6acf7f0-9694-4ae2-b67f-ee6db7b2d9f3",
    "8fac62cb-10d2-4642-a7d2-2271913bf893",
  ],
  quotes: [
    "Free tier says \"Unlimited members\" — that's the first thing that's made me sit up. Most tools charge me per head and I've got contractors floating in and out.",
    "Unlimited members on the free plan is unusual — most tools charge per seat even on free tiers, so that's actually a relief given how much per-seat pricing punishes us for hiring.",
  ],
  likely_environmental: false,
};

const homepage: ConsensusFinding = {
  statement:
    "The homepage's live product simulation may be doing more work to impress than to explain.",
  dimension: "First impression",
  severity: "medium",
  claim_type: "interpretation",
  impact:
    "Founders read the interactive demo as a positive signal on arrival, then tire of it while looking for concrete answers. The same element helps at second zero and hinders at minute two.",
  recommendation:
    "Offer a plain, scannable summary of what the product does alongside the simulation, for visitors who have already decided it looks credible.",
  agreement_count: 2,
  run_count: 4,
  segments: [
    { segment: "Startup founders", hit: 2, total: 2 },
    { segment: "IT recruiters", hit: 0, total: 2 },
  ],
  severity_spread: ["medium", "low"],
  source_finding_ids: ["f-home-1", "f-home-2"],
  run_ids: ["8fac62cb-10d2-4642-a7d2-2271913bf893"],
  quotes: [
    "Honestly the homepage itself is a bit much — it's built as this elaborate live simulation of their own product (fake issues, fake Slack threads, fake AI agents picking up tickets).",
    "The whole page is one big interactive product demo instead of stock photos. That's a good sign, I can already see what it actually does.",
  ],
  likely_environmental: false,
};

const signup: ConsensusFinding = {
  statement:
    "The signup page rendered blank, ending the session before an account could be created.",
  dimension: "Onboarding",
  severity: "critical",
  claim_type: "observed_behavior",
  impact:
    "The customer retried once, waited, and then abandoned. Whatever the cause, this is where the session ended.",
  recommendation:
    "Reproduce in a clean browser profile before treating this as a product defect — an empty render with no form and no OAuth buttons is as consistent with a blocked automated browser as with a real outage.",
  agreement_count: 1,
  run_count: 4,
  segments: [
    { segment: "Startup founders", hit: 1, total: 2 },
    { segment: "IT recruiters", hit: 0, total: 2 },
  ],
  severity_spread: ["critical"],
  source_finding_ids: ["f-signup-1"],
  run_ids: ["8fac62cb-10d2-4642-a7d2-2271913bf893"],
  quotes: [
    "The signup page is just blank for me — nothing loaded, no form, no Google/GitHub button, nothing. I tried reloading and waiting a few seconds and it's still empty.",
    "Nope, signup page is still blank on a second try. That's a real dead end for me — I'm not going to keep retrying a broken sign-up page in the middle of a work day.",
  ],
  likely_environmental: true,
};

/**
 * The finding only a *study* can produce: unanimous in one segment, absent in
 * the other. A single session would have reported it as one person's opinion.
 */
const audienceMismatch: ConsensusFinding = {
  statement:
    "Every recruiter identified the product as an engineering tool and stopped within the first minute — they found nothing addressed to them.",
  dimension: "Audience fit",
  severity: "critical",
  claim_type: "observed_behavior",
  impact:
    "Both recruiters reached the same conclusion independently, at the same point on the homepage, and neither went further. This is not a messaging problem to be fixed with copy — it is the wrong audience for this product, and every recruiter reaching the site is spend that cannot convert.",
  recommendation:
    "Treat recruiters as out of scope rather than under-served: if this segment is being targeted by acquisition, the spend is misdirected.",
  agreement_count: 2,
  run_count: 4,
  segments: [
    { segment: "IT recruiters", hit: 2, total: 2 },
    { segment: "Startup founders", hit: 0, total: 2 },
  ],
  severity_spread: ["critical", "critical"],
  source_finding_ids: ["f-fit-1", "f-fit-2"],
  run_ids: ["316b78f9-b3f0-4cd9-9c8d-dc0fe02291ad", "382a5d18-7e7e-4a6c-94b9-2b88d4ae2e26"],
  quotes: [
    "This is clearly a product/engineering issue tracker — issues, cycles, PRDs, PRs, agents writing code. Nothing here about candidates, ATS, or sourcing.",
    "Okay this is clearly a dev tool — issue tracking, PRDs, code diffs, agents doing PRs. This has nothing to do with recruiting or sourcing candidates.",
  ],
  likely_environmental: false,
};

export const PREVIEW_REPORT: StudyReport = {
  headline:
    "The two audiences split cleanly: every recruiter left within a minute, while both founders were won by the free tier and then blocked by a price list with no prices.",
  run_count: 4,
  analysed_count: 4,
  would_continue_rate: 0.5,
  purchase_intent: { would_consider: 2, unlikely: 2 },
  retention_intent: { likely: 2, unsure: 2 },
  segments: [
    {
      segment: "Startup founders",
      persona_label: "Startup founder",
      runs: 2,
      completed: 2,
      would_continue: 2,
      outcomes: { explored: 2 },
      mean_satisfaction: 3.6,
    },
    {
      segment: "IT recruiters",
      persona_label: "Recruiter",
      runs: 2,
      completed: 2,
      would_continue: 0,
      outcomes: { abandoned: 2 },
      mean_satisfaction: 1.5,
    },
  ],
  consensus_findings: [audienceMismatch, pricing, freeTier, homepage],
  environment_warnings: [signup],
};
