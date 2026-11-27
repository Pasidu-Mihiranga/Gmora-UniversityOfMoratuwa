import { NavLink, Outlet } from "react-router-dom";

import { useT } from "@/shared/i18n";
import { Brand, Icon } from "@/shared/ui";

import { useDisplayMode } from "./displayMode";
import { NAV } from "./nav";
import { HOME_BY_ROLE, useSession } from "./session";

/** Sidebar on desktop, top bar always, bottom tab bar (first four items) on mobile. */
export function AppShell() {
  const { user, signOut } = useSession();
  const { t, language, setLanguage } = useT();
  const { mode, setMode } = useDisplayMode(user?.role === "patient" ? "simple" : "standard");
  if (!user) return null;
  const items = NAV[user.role];

  const link = ({ isActive }: { isActive: boolean }) => `sp-nav-link ${isActive ? "sp-nav-link-active" : ""}`;

  return (
    <div className="min-h-screen lg:flex">
      <aside className="hidden w-sidebar shrink-0 flex-col border-r border-line bg-surface lg:sticky lg:top-0 lg:flex lg:h-screen">
        <div className="flex h-topbar items-center border-b border-line px-4">
          <Brand />
        </div>
        <nav aria-label="Primary" className="flex-1 space-y-1 overflow-y-auto p-3">
          {items.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === HOME_BY_ROLE[user.role]} className={link}>
              <Icon name={item.icon} size={20} />
              <span>{t(item.label)}</span>
            </NavLink>
          ))}
        </nav>
      </aside>

      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-30 flex h-topbar items-center gap-2 border-b border-line bg-surface/95 px-4 backdrop-blur">
          <span className="lg:hidden">
            <Brand variant="mark" />
          </span>
          {user.preview ? <span className="sp-chip sp-chip-warn">Preview</span> : null}
          <div className="ml-auto flex items-center gap-2">
            <button type="button" className="sp-btn sp-btn-ghost min-h-tap"
                    onClick={() => setLanguage(language === "si" ? "en" : "si")} aria-label={t("common.language")}>
              <Icon name="language" size={20} />
              {language === "si" ? "English" : "සිංහල"}
            </button>
            <button type="button" className="sp-btn sp-btn-ghost min-h-tap" aria-pressed={mode === "simple"}
                    onClick={() => setMode(mode === "simple" ? "standard" : "simple")}>
              <Icon name="visibility" size={20} />
              Aa
            </button>
            <button type="button" className="sp-btn sp-btn-ghost min-h-tap" onClick={() => void signOut()}>
              <Icon name="logout" size={20} />
              <span className="hidden sm:inline">{t("common.signOut")}</span>
            </button>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6 pb-28 lg:px-8 lg:pb-8">
          <Outlet />
        </main>
      </div>

      <nav className="sp-tabbar lg:hidden" aria-label="Primary">
        {items.slice(0, 4).map((item) => (
          <NavLink key={item.to} to={item.to} end={item.to === HOME_BY_ROLE[user.role]}
                   className={({ isActive }) => `sp-tab flex min-w-0 shrink-0 flex-col items-center justify-center px-1 ${isActive ? "sp-tab-active" : ""}`}>
            <Icon name={item.icon} size={22} />
            <span className="truncate text-xs">{t(item.label)}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
