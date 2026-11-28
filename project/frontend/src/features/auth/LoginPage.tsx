import { useEffect, useState, type FormEvent } from "react";
import { Link, Navigate } from "react-router-dom";

import { HOME_BY_ROLE, useSession, type Role } from "@/app/session";
import { useT } from "@/shared/i18n";
import { Icon, Notice, type IconName } from "@/shared/ui";

import { AuthLayout } from "./AuthLayout";
import { GoogleButton } from "./GoogleButton";

interface DemoAccount {
  role: Role;
  login: string;
  display_name: string;
  password: string;
}

const ROLE_ICON: Record<Role, IconName> = {
  patient: "person",
  guardian: "group",
  doctor: "stethoscope",
  hospital_admin: "hospital",
  system_admin: "shield",
};

/** The five demo sign-ins of spec 16. The route exists only in development and demo builds of the
 *  API, so in production the panel simply does not appear. Tapping fills the form; signing in is
 *  still the normal path. */
function useDemoAccounts(): DemoAccount[] {
  const [accounts, setAccounts] = useState<DemoAccount[]>([]);
  useEffect(() => {
    let live = true;
    fetch("/api/v1/auth/demo-accounts")
      .then(async (r) => (r.ok ? ((await r.json()) as DemoAccount[]) : []))
      .then((list) => live && setAccounts(list))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);
  return accounts;
}

export default function LoginPage() {
  const { user, signIn, previewAs } = useSession();
  const { t } = useT();
  const demo = useDemoAccounts();
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);

  if (user) return <Navigate to={HOME_BY_ROLE[user.role]} replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setFailed(!(await signIn(login, password)));
    setBusy(false);
  }

  function fill(account: DemoAccount) {
    setLogin(account.login);
    setPassword(account.password);
    setPicked(account.login);
    setFailed(false);
  }

  return (
    <AuthLayout title={t("login.welcome")} subtitle={t("login.subtitle")}>
      <form className="space-y-4" onSubmit={(e) => void submit(e)}>
        <label className="sp-field block">
          <span className="sp-label">{t("login.login")}</span>
          <span className="relative block">
            <Icon name="person" size={20} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-400" />
            <input className="sp-input min-h-tap w-full pl-10" autoComplete="username" value={login} maxLength={160}
                   onChange={(e) => setLogin(e.target.value)} required />
          </span>
        </label>
        <label className="sp-field block">
          <span className="sp-label">{t("login.password")}</span>
          <span className="relative block">
            <Icon name="lock" size={20} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-400" />
            <input className="sp-input min-h-tap w-full pl-10 pr-12" type={show ? "text" : "password"}
                   autoComplete="current-password" value={password} maxLength={200}
                   onChange={(e) => setPassword(e.target.value)} required />
            <button type="button" className="absolute inset-y-0 right-0 flex min-w-tap items-center justify-center text-ink-400 hover:text-ink-600"
                    aria-label={show ? t("login.hide_password") : t("login.show_password")} onClick={() => setShow(!show)}>
              <Icon name={show ? "visibilityOff" : "visibility"} size={20} />
            </button>
          </span>
        </label>
        {failed ? <Notice tone="danger">{t("login.failed")}</Notice> : null}
        <button type="submit" className="sp-btn sp-btn-primary sp-btn-block min-h-tap gap-2" disabled={busy}>
          {t("login.submit")}
          <Icon name="arrowRight" size={18} />
        </button>
      </form>

      <div className="my-4 flex items-center gap-3 text-sm text-ink-500" aria-hidden>
        <span className="h-px flex-1 bg-line" />
        {t("login.or")}
        <span className="h-px flex-1 bg-line" />
      </div>
      <GoogleButton />

      {demo.length > 0 ? (
        <section className="mt-5" aria-labelledby="demo-title">
          <p id="demo-title" className="text-center text-sm font-semibold text-ink-700">{t("login.demo_title")}</p>
          <p className="mb-2 text-center text-xs text-ink-500">{t("login.demo_hint")}</p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-2">
            {demo.map((account) => (
              <button key={account.login} type="button" onClick={() => fill(account)}
                      aria-pressed={picked === account.login}
                      className={`flex min-h-tap items-center gap-2 rounded-xl border px-3 py-2 text-left transition hover:bg-brand-50 ${
                        picked === account.login ? "border-brand-500 bg-brand-50" : "border-line"
                      }`}>
                <Icon name={ROLE_ICON[account.role]} size={20} className="shrink-0 text-brand-600" />
                <span className="min-w-0">
                  <span className="block text-sm font-semibold text-ink-800">{t(`role.${account.role}`)}</span>
                  <span className="block truncate text-xs text-ink-500">{account.display_name}</span>
                </span>
              </button>
            ))}
          </div>
        </section>
      ) : null}

      <p className="mt-5 text-center text-ink-600">
        {t("login.no_account")}{" "}
        <Link to="/register" className="font-semibold text-brand-700 underline-offset-2 hover:underline">{t("login.create")}</Link>
      </p>

      {import.meta.env.DEV && previewAs && demo.length === 0 ? (
        <div className="mt-4 space-y-2 border-t border-line pt-4">
          <p className="sp-label">{t("login.preview")}</p>
          <div className="flex flex-wrap gap-2">
            {(Object.keys(ROLE_ICON) as Role[]).map((role) => (
              <button key={role} type="button" className="sp-btn sp-btn-secondary sp-btn-sm" onClick={() => previewAs(role)}>
                {t(`role.${role}`)}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </AuthLayout>
  );
}
