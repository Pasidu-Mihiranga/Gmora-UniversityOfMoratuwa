import { useT, type MessageKey } from "@/shared/i18n";
import { Notice } from "@/shared/ui";

/** Wave 0 placeholder (FE-3). The issue named here replaces the page in its feature folder. */
export function placeholder(title: MessageKey, issue: string) {
  return function PlaceholderPage() {
    const { t } = useT();
    return (
      <section className="space-y-4">
        <h1 className="sp-display text-2xl">{t(title)}</h1>
        <Notice tone="info">
          {t("common.comingSoon")} ({issue})
        </Notice>
      </section>
    );
  };
}
