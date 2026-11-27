import { useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";

import { HOME_BY_ROLE, useSession, type Role } from "@/app/session";
import { useT } from "@/shared/i18n";
import { Brand, Notice } from "@/shared/ui";

const PREVIEW_ROLES: Role[] = ["patient", "guardian", "doctor", "hospital_admin", "system_admin"];

export default function LoginPage() {
  const { user, signIn, previewAs } = useSession();
  const { t, language, setLanguage } = useT();
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to={HOME_BY_ROLE[user.role]} replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setFailed(!(await signIn(login, password)));
    setBusy(false);
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-canvas p-4">
      <div className="sp-card sp-card-pad w-full max-w-md space-y-6">
        <div className="flex items-center justify-between">
          <Brand />
          <button type="button" className="sp-btn sp-btn-ghost min-h-tap"
                  onClick={() => setLanguage(language === "si" ? "en" : "si")}>
            {language === "si" ? "English" : "සිංහල"}
          </button>
        </div>
        <h1 className="sp-display text-2xl">{t("login.title")}</h1>
        <form className="space-y-4" onSubmit={(e) => void submit(e)}>
          <label className="sp-field block">
            <span className="sp-label">{t("login.login")}</span>
            <input className="sp-input min-h-tap" autoComplete="username" value={login} maxLength={160}
                   onChange={(e) => setLogin(e.target.value)} required />
          </label>
          <label className="sp-field block">
            <span className="sp-label">{t("login.password")}</span>
            <input className="sp-input min-h-tap" type="password" autoComplete="current-password" value={password}
                   maxLength={200} onChange={(e) => setPassword(e.target.value)} required />
          </label>
          {failed ? <Notice tone="danger">{t("login.failed")}</Notice> : null}
          <button type="submit" className="sp-btn sp-btn-primary sp-btn-block min-h-tap" disabled={busy}>
            {t("login.submit")}
          </button>
        </form>
        {import.meta.env.DEV && previewAs ? (
          <div className="space-y-2 border-t border-line pt-4">
            <p className="sp-label">{t("login.preview")}</p>
            <div className="flex flex-wrap gap-2">
              {PREVIEW_ROLES.map((role) => (
                <button key={role} type="button" className="sp-btn sp-btn-secondary sp-btn-sm" onClick={() => previewAs(role)}>
                  {t(`role.${role}`)}
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </main>
  );
}
