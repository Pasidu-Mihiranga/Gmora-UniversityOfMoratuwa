/** Every route (spec 12 section 4), declared once in Wave 0. Pages are lazy loaded (FE-16). */
import { lazy, Suspense, type ComponentType, type ReactNode } from "react";
import { createBrowserRouter, Navigate, Outlet, useRouteError } from "react-router-dom";

import { useT } from "@/shared/i18n";
import { Notice, SkeletonCard, Spinner } from "@/shared/ui";

import { AppShell } from "./AppShell";
import { HOME_BY_ROLE, useSession, type Role } from "./session";

type PageModule = Record<string, ComponentType>;

function page(load: () => Promise<PageModule>, name: string) {
  const Page = lazy(async () => ({ default: (await load())[name] as ComponentType }));
  return (
    <Suspense fallback={<SkeletonCard lines={4} />}>
      <Page />
    </Suspense>
  );
}

const home = () => import("@/features/home/pages");
const companion = () => import("@/features/companion/pages");
const record = () => import("@/features/record/pages");
const consent = () => import("@/features/consent/pages");
const care = () => import("@/features/care/pages");
const booking = () => import("@/features/booking/pages");
const reports = () => import("@/features/reports/pages");
const imaging = () => import("@/features/imaging/pages");
const programmes = () => import("@/features/programmes/pages");
const privateConsult = () => import("@/features/private/pages");
const reviews = () => import("@/features/reviews/pages");
const notifications = () => import("@/features/notifications/pages");
const settings = () => import("@/features/settings/pages");
const guardian = () => import("@/features/guardian/pages");
const doctor = () => import("@/features/doctor/pages");
const hospital = () => import("@/features/hospital/pages");
const ops = () => import("@/features/ops/pages");
const dev = () => import("@/features/dev/pages");
const LoginPage = lazy(() => import("@/features/auth/LoginPage"));

function RequireRole({ roles }: { roles: Role[] }) {
  const { user, loading } = useSession();
  if (loading) return <Spinner />;
  if (!user) return <Navigate to="/login" replace />;
  if (!roles.includes(user.role)) return <Navigate to={HOME_BY_ROLE[user.role]} replace />;
  return <Outlet />;
}

function RoleHome() {
  const { user, loading } = useSession();
  if (loading) return <Spinner />;
  return <Navigate to={user ? HOME_BY_ROLE[user.role] : "/login"} replace />;
}

function RouteError() {
  const error = useRouteError();
  const { t } = useT();
  console.error(error);
  return (
    <div className="p-6">
      <Notice tone="danger" title={t("common.error")}>
        <button type="button" className="sp-btn sp-btn-secondary mt-2" onClick={() => window.location.reload()}>
          {t("common.retry")}
        </button>
      </Notice>
    </div>
  );
}

const shared = (prefix: string): { path: string; element: ReactNode }[] => [
  { path: `${prefix}/notifications`, element: page(notifications, "NotificationsPage") },
  { path: `${prefix}/settings`, element: page(settings, "AssistantSettingsPage") },
];

export const router = createBrowserRouter([
  { path: "/login", element: <Suspense fallback={<Spinner />}><LoginPage /></Suspense>, errorElement: <RouteError /> },
  {
    element: <AppShell />,
    errorElement: <RouteError />,
    children: [
      {
        element: <RequireRole roles={["patient"]} />,
        children: [
          { path: "/patient", element: page(home, "PatientHome") },
          { path: "/patient/companion", element: page(companion, "CompanionPage") },
          { path: "/patient/companion/:caseId", element: page(companion, "CompanionPage") },
          { path: "/patient/record", element: page(record, "RecordPage") },
          { path: "/patient/sharing", element: page(consent, "SharingPage") },
          { path: "/patient/medicines", element: page(care, "MedicinesPage") },
          { path: "/patient/appointments", element: page(booking, "AppointmentsPage") },
          { path: "/patient/find-care", element: page(booking, "FindCarePage") },
          { path: "/patient/reports", element: page(reports, "ReportsPage") },
          { path: "/patient/imaging", element: page(imaging, "ImagingPage") },
          { path: "/patient/programmes", element: page(programmes, "ProgrammesPage") },
          { path: "/patient/private", element: page(privateConsult, "PrivateConsultPage") },
          { path: "/patient/reviews", element: page(reviews, "ReviewsPage") },
          ...shared("/patient"),
        ],
      },
      {
        element: <RequireRole roles={["guardian"]} />,
        children: [
          { path: "/guardian", element: page(guardian, "DependentsPage") },
          { path: "/guardian/dependents/:patientId", element: page(guardian, "DependentDetailPage") },
          { path: "/guardian/alerts", element: page(guardian, "AlertsPage") },
          { path: "/guardian/rules", element: page(guardian, "AlertRulesPage") },
          ...shared("/guardian"),
        ],
      },
      {
        element: <RequireRole roles={["doctor"]} />,
        children: [
          { path: "/doctor", element: page(doctor, "QueuePage") },
          { path: "/doctor/cases/:caseId", element: page(doctor, "CaseBriefPage") },
          { path: "/doctor/approvals", element: page(doctor, "ApprovalsPage") },
          { path: "/doctor/reviews", element: page(reviews, "ReviewsPage") },
          ...shared("/doctor"),
        ],
      },
      {
        element: <RequireRole roles={["hospital_admin"]} />,
        children: [
          { path: "/hospital", element: page(hospital, "SessionsPage") },
          { path: "/hospital/sessions/:sessionId", element: page(hospital, "SessionDetailPage") },
          { path: "/hospital/offerings", element: page(hospital, "OfferingsPage") },
        ],
      },
      {
        element: <RequireRole roles={["system_admin"]} />,
        children: [
          { path: "/admin", element: page(ops, "AiOpsPage") },
          { path: "/admin/programmes", element: page(ops, "AdminProgrammesPage") },
          { path: "/admin/audit", element: page(ops, "AuditPage") },
          { path: "/admin/budgets", element: page(ops, "BudgetsPage") },
          { path: "/admin/dead-letters", element: page(ops, "DeadLettersPage") },
          { path: "/admin/flags", element: page(ops, "FlagsPage") },
        ],
      },
      {
        element: <RequireRole roles={["patient", "guardian", "doctor", "hospital_admin", "system_admin"]} />,
        children: import.meta.env.DEV ? [{ path: "/dev", element: page(dev, "DemoControlsPage") }] : [],
      },
    ],
  },
  { path: "*", element: <RoleHome /> },
]);
