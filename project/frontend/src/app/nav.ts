/** Navigation per role (spec 12 section 4). Fixed in Wave 0; feature issues do not edit it (FE-3). */
import type { MessageKey } from "@/shared/i18n";
import type { IconName } from "@/shared/ui";

import type { Role } from "./session";

export interface NavItem {
  to: string;
  label: MessageKey;
  icon: IconName;
}

export const NAV: Record<Role, NavItem[]> = {
  patient: [
    { to: "/patient", label: "nav.home", icon: "home" },
    { to: "/patient/companion", label: "nav.companion", icon: "chat" },
    { to: "/patient/medicines", label: "nav.medicines", icon: "medication" },
    { to: "/patient/appointments", label: "nav.appointments", icon: "calendar" },
    { to: "/patient/find-care", label: "nav.findCare", icon: "search" },
    { to: "/patient/record", label: "nav.record", icon: "description" },
    { to: "/patient/reports", label: "nav.reports", icon: "lab" },
    { to: "/patient/imaging", label: "nav.imaging", icon: "scan" },
    { to: "/patient/programmes", label: "nav.programmes", icon: "elderly" },
    { to: "/patient/private", label: "nav.private", icon: "privacy" },
    { to: "/patient/sharing", label: "nav.sharing", icon: "shield" },
    { to: "/patient/reviews", label: "nav.reviews", icon: "verified" },
    { to: "/patient/notifications", label: "nav.notifications", icon: "notifications" },
    { to: "/patient/settings", label: "nav.settings", icon: "settings" },
  ],
  guardian: [
    { to: "/guardian", label: "nav.dependents", icon: "group" },
    { to: "/guardian/alerts", label: "nav.alerts", icon: "warning" },
    { to: "/guardian/rules", label: "nav.rules", icon: "filter" },
    { to: "/guardian/notifications", label: "nav.notifications", icon: "notifications" },
    { to: "/guardian/settings", label: "nav.settings", icon: "settings" },
  ],
  doctor: [
    { to: "/doctor", label: "nav.queue", icon: "queue" },
    { to: "/doctor/approvals", label: "nav.approvals", icon: "verified" },
    { to: "/doctor/reviews", label: "nav.reviews", icon: "check" },
    { to: "/doctor/notifications", label: "nav.notifications", icon: "notifications" },
    { to: "/doctor/settings", label: "nav.settings", icon: "settings" },
  ],
  hospital_admin: [
    { to: "/hospital", label: "nav.sessions", icon: "calendar" },
    { to: "/hospital/offerings", label: "nav.offerings", icon: "hospital" },
  ],
  system_admin: [
    { to: "/admin", label: "nav.aiOps", icon: "ai" },
    { to: "/admin/programmes", label: "nav.adminProgrammes", icon: "elderly" },
    { to: "/admin/audit", label: "nav.audit", icon: "audit" },
    { to: "/admin/budgets", label: "nav.budgets", icon: "payments" },
    { to: "/admin/dead-letters", label: "nav.deadLetters", icon: "error" },
    { to: "/admin/flags", label: "nav.flags", icon: "bolt" },
  ],
};
