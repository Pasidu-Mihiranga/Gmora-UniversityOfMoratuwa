/** One key factory per feature (FE-11). Feature issues add their keys here. */
export const queryKeys = {
  me: ["me"] as const,
  specialties: ["specialties"] as const,
  programmes: ["programmes"] as const,
  cases: {
    all: ["cases"] as const,
    detail: (caseId: string) => ["cases", caseId] as const,
    timeline: (caseId: string) => ["cases", caseId, "timeline"] as const,
  },
  notifications: ["notifications"] as const,
} as const;
