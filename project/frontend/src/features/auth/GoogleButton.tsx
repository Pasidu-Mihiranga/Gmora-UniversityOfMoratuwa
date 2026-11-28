/**
 * "Continue with Google": planned (OAuth is not built yet), so it is shown but disabled with a
 * "Coming soon" label, never a button that pretends to work.
 */
import { useT } from "@/shared/i18n";

function GoogleMark() {
  return (
    <svg width="20" height="20" viewBox="0 0 48 48" aria-hidden focusable="false">
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3 0 5.8 1.1 7.9 3l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5Z" />
      <path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3 0 5.8 1.1 7.9 3l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7Z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44Z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5Z" />
    </svg>
  );
}

export function GoogleButton() {
  const { t } = useT();
  return (
    <button type="button" disabled aria-disabled="true" title={t("login.soon")}
            className="sp-btn sp-btn-secondary sp-btn-block min-h-tap cursor-not-allowed justify-center gap-3 opacity-80">
      <GoogleMark />
      <span>{t("login.google")}</span>
      <span className="rounded-full bg-ink-100 px-2 py-0.5 text-xs font-semibold text-ink-600">{t("login.soon")}</span>
    </button>
  );
}
