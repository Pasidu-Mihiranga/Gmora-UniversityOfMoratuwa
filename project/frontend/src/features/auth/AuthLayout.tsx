/**
 * Two-panel layout for sign in and sign up: artwork and the promise on the left (desktop), a
 * compact hero with a bottom sheet on phones. The page itself never scrolls; only the sheet does.
 */
import type { ReactNode } from "react";

import { useT } from "@/shared/i18n";
import { Icon } from "@/shared/ui";

export function AuthLayout({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  const { t, language, setLanguage } = useT();
  const otherLanguage = (
    <button type="button" className="sp-btn sp-btn-ghost sp-btn-sm min-h-tap"
            onClick={() => setLanguage(language === "si" ? "en" : "si")}>
      <Icon name="language" size={18} />
      {language === "si" ? "English" : "සිංහල"}
    </button>
  );
  return (
    <div className="flex h-[100dvh] flex-col overflow-hidden bg-brand-50 lg:items-center lg:justify-center lg:bg-canvas lg:p-6">
      <div className="flex min-h-0 w-full max-w-[1000px] flex-1 flex-col overflow-hidden lg:grid lg:max-h-[calc(100dvh-3rem)] lg:flex-none lg:grid-cols-2 lg:rounded-3xl lg:bg-surface lg:shadow-lg">
        <aside className="relative hidden min-h-0 flex-col items-center overflow-hidden bg-brand-50 px-10 pt-10 lg:flex">
          <img src="/brand/mark.png" alt="" aria-hidden className="mb-4 h-[clamp(56px,9vh,92px)] w-auto object-contain" />
          <p className="sp-display text-[clamp(24px,3.2vh,32px)] text-brand-900">
            SuwaPath <span className="font-medium">-</span> <span className="text-brand-500">සුවපත්</span>
          </p>
          <p className="mt-4 text-xl font-bold text-brand-900">{t("login.tagline")}</p>
          <p className="mt-2 max-w-sm text-center text-ink-600">{t("login.pitch")}</p>
          <div className="flex min-h-0 w-full flex-1 items-end justify-center pb-6">
            <img src="/illustrations/login-desktop.webp" alt="" aria-hidden loading="eager"
                 className="max-h-full w-[115%] max-w-none object-contain object-bottom mix-blend-multiply" />
          </div>
        </aside>

        <div className="flex min-h-0 w-full flex-1 flex-col overflow-hidden lg:items-center lg:px-10 lg:py-8">
          <div className="flex h-[26dvh] shrink-0 flex-col items-center px-5 pt-[max(0.75rem,env(safe-area-inset-top))] text-center lg:hidden [@media(max-height:700px)]:h-auto">
            <div className="flex w-full justify-end">{otherLanguage}</div>
            <img src="/brand/mark.png" alt="" aria-hidden className="h-[clamp(40px,7vh,68px)] w-auto object-contain" />
            <p className="sp-display mt-1 text-[clamp(18px,2.6vh,24px)] text-brand-900">
              SuwaPath <span className="text-brand-500">සුවපත්</span>
            </p>
            <div className="flex min-h-0 w-full max-w-[min(80%,360px)] flex-1 items-center justify-center py-1 [@media(max-height:700px)]:hidden">
              <img src="/illustrations/login-mobile.webp" alt="" aria-hidden loading="eager"
                   className="h-full max-h-full w-full object-contain mix-blend-multiply" />
            </div>
          </div>

          <div className="mx-auto flex min-h-0 w-full flex-1 flex-col overflow-y-auto overscroll-contain rounded-t-[28px] bg-surface px-[max(1.25rem,calc((100%-24rem)/2))] pb-[max(1rem,env(safe-area-inset-bottom))] pt-5 shadow-[0_-6px_24px_rgba(10,46,86,0.10)] lg:max-h-full lg:max-w-sm lg:flex-1 lg:overflow-y-auto lg:rounded-none lg:bg-transparent lg:px-1 lg:py-2 lg:shadow-none">
            <div className="mx-auto mb-3 h-1 w-10 shrink-0 rounded-full bg-ink-200 lg:hidden" aria-hidden />
            <div className="mb-5 flex items-start justify-between gap-3">
              <div>
                <h1 className="sp-display text-[clamp(20px,3vh,28px)] leading-tight text-brand-900">{title}</h1>
                <p className="mt-1 text-ink-500">{subtitle}</p>
              </div>
              <div className="hidden lg:block">{otherLanguage}</div>
            </div>
            {children}
            <p className="mt-4 flex shrink-0 items-center justify-center gap-1.5 text-sm text-ink-500">
              <Icon name="verified" size={16} className="text-brand-600" />
              {t("login.secure")}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
