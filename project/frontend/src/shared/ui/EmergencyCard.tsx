import { useT } from "../i18n";
import Icon from "./Icon";

/** FE-25: full screen, cannot be dismissed by accident; the call button dials 1990. */
export function EmergencyCard({ message }: { message?: string }) {
  const { t } = useT();
  return (
    <div role="alertdialog" aria-modal="true" aria-labelledby="emergency-title"
         className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-6 bg-danger-solid p-8 text-center text-white">
      <Icon name="emergency" size={64} />
      <h1 id="emergency-title" className="text-3xl font-bold text-white">
        {t("urgency.emergency")}
      </h1>
      {message ? <p className="max-w-md text-lg">{message}</p> : null}
      <a href="tel:1990" className="sp-btn sp-btn-jumbo min-h-tap bg-white text-danger-text">
        {t("emergency.call")}
      </a>
    </div>
  );
}
