import { useState, type FormEvent } from "react";
import { Link, Navigate } from "react-router-dom";

import { HOME_BY_ROLE, useSession, type Registration } from "@/app/session";
import { useT } from "@/shared/i18n";
import type { MessageKey } from "@/shared/i18n/en";
import { Icon, Notice, type IconName } from "@/shared/ui";

import { AuthLayout } from "./AuthLayout";
import { GoogleButton } from "./GoogleButton";

const KINDS: { role: Registration["role"]; icon: IconName; label: MessageKey }[] = [
  { role: "patient", icon: "person", label: "register.as_patient" },
  { role: "guardian", icon: "group", label: "register.as_guardian" },
];

/** Self sign-up for patients and family members. Doctors and staff are added by their hospital. */
export default function RegisterPage() {
  const { user, register } = useSession();
  const { t, language } = useT();
  const [form, setForm] = useState<Registration>({
    role: "patient",
    display_name: "",
    login: "",
    password: "",
    language,
  });
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to={HOME_BY_ROLE[user.role]} replace />;

  const set = <K extends keyof Registration>(key: K, value: Registration[K]) => setForm({ ...form, [key]: value });

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setFailed(!(await register(form)));
    setBusy(false);
  }

  return (
    <AuthLayout title={t("register.title")} subtitle={t("register.subtitle")}>
      <form className="space-y-4" onSubmit={(e) => void submit(e)}>
        <fieldset>
          <legend className="sp-label mb-1">{t("register.as")}</legend>
          <div className="grid grid-cols-2 gap-2">
            {KINDS.map((kind) => (
              <button key={kind.role} type="button" aria-pressed={form.role === kind.role} onClick={() => set("role", kind.role)}
                      className={`flex min-h-tap items-center gap-2 rounded-xl border px-3 py-2 text-left font-semibold transition ${
                        form.role === kind.role ? "border-brand-500 bg-brand-50 text-brand-800" : "border-line text-ink-700 hover:bg-brand-50"
                      }`}>
                <Icon name={kind.icon} size={20} className="text-brand-600" />
                {t(kind.label)}
              </button>
            ))}
          </div>
          {form.role === "guardian" ? <p className="mt-2 text-sm text-ink-500">{t("register.guardian_note")}</p> : null}
        </fieldset>
        <label className="sp-field block">
          <span className="sp-label">{t("register.name")}</span>
          <input className="sp-input min-h-tap w-full" autoComplete="name" value={form.display_name} maxLength={100}
                 onChange={(e) => set("display_name", e.target.value)} required />
        </label>
        <label className="sp-field block">
          <span className="sp-label">{t("login.login")}</span>
          <input className="sp-input min-h-tap w-full" autoComplete="username" value={form.login} maxLength={160}
                 onChange={(e) => set("login", e.target.value)} required />
        </label>
        <label className="sp-field block">
          <span className="sp-label">{t("login.password")}</span>
          <input className="sp-input min-h-tap w-full" type="password" autoComplete="new-password" value={form.password}
                 minLength={8} maxLength={200} onChange={(e) => set("password", e.target.value)} required />
          <span className="mt-1 block text-sm text-ink-500">{t("register.password_hint")}</span>
        </label>
        <label className="sp-field block">
          <span className="sp-label">{t("register.language")}</span>
          <select className="sp-select min-h-tap w-full" value={form.language}
                  onChange={(e) => set("language", e.target.value as Registration["language"])}>
            <option value="si">සිංහල</option>
            <option value="en">English</option>
          </select>
        </label>
        {failed ? <Notice tone="danger">{t("register.failed")}</Notice> : null}
        <button type="submit" className="sp-btn sp-btn-primary sp-btn-block min-h-tap" disabled={busy}>
          {t("register.submit")}
        </button>
      </form>
      <div className="my-4 flex items-center gap-3 text-sm text-ink-500" aria-hidden>
        <span className="h-px flex-1 bg-line" />
        {t("login.or")}
        <span className="h-px flex-1 bg-line" />
      </div>
      <GoogleButton />
      <p className="mt-5 text-center text-ink-600">
        {t("register.have_account")}{" "}
        <Link to="/login" className="font-semibold text-brand-700 underline-offset-2 hover:underline">{t("login.submit")}</Link>
      </p>
      <p className="mt-2 text-center text-sm text-ink-500">{t("register.staff_note")}</p>
    </AuthLayout>
  );
}
