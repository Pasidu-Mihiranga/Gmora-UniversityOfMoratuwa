/** Visual tone for a status value, in one place (spec 12). Keys are backend enum values. */
export type Tone = "neutral" | "ok" | "warn" | "danger" | "info" | "programme";

const TONE_BY_STATUS: Record<string, Tone> = {
  // urgency (Urgency enum)
  emergency: "danger",
  urgent_24h: "warn",
  soon_72h: "info",
  routine: "ok",
  // appointments (AppointmentStatus)
  held: "warn",
  booked: "info",
  fulfilled: "ok",
  cancelled: "neutral",
  noshow: "danger",
  // documents (DocumentStatus)
  processing: "info",
  ready: "ok",
  failed: "danger",
  needs_manual_entry: "warn",
  pending_scan: "info",
  // imaging bands, doctor views only (IMG-5)
  no_clear_signs: "ok",
  uncertain: "warn",
  possible: "warn",
  signs: "danger",
  screening_unavailable: "neutral",
  // lab flags
  low: "warn",
  normal: "ok",
  high: "warn",
  abnormal: "danger",
  // alerts and reviews
  info: "info",
  concern: "warn",
  urgent: "danger",
  pending: "warn",
  approved: "ok",
  rejected: "danger",
};

export function toneFor(status?: string | null): Tone {
  if (!status) return "neutral";
  return TONE_BY_STATUS[status] ?? "neutral";
}
