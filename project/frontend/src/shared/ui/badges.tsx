import { useT } from "../i18n";
import Icon from "./Icon";

/** FE-22: output from offline mode (no model keys). Never shown in the judged demo. */
export function DemoModeBadge() {
  const { t } = useT();
  return (
    <span className="sp-chip sp-chip-info" role="note">
      <Icon name="info" size={14} />
      {t("badge.demoMode")}
    </span>
  );
}

/** FE-22: the verifier ran its rules only (no model verifier configured, VER-10). */
export function RulesOnlyBadge() {
  const { t } = useT();
  return (
    <span className="sp-chip sp-chip-neutral" role="note">
      {t("badge.rulesOnly")}
    </span>
  );
}

/** FE-22: imaging lines for doctors when the model's metrics are not validated (IMG-4). */
export function UnvalidatedModelBadge() {
  const { t } = useT();
  return (
    <span className="sp-chip sp-chip-warn" role="note">
      {t("badge.unvalidatedModel")}
    </span>
  );
}
