import { useT } from "../i18n";

/** Mirrors contracts.suggestions.Suggestion (AGT-36). */
export interface Suggestion {
  kind: "reply" | "action";
  label: string;
  fill_text?: string | null;
  action_id?: string | null;
  action_kind?: string | null;
}

interface Props {
  suggestions: readonly Suggestion[];
  /** reply chip: put the text in the input bar for the person to edit and send (FE-51). */
  onFill: (text: string) => void;
  /** action chip: open the confirmation step; never completes an irreversible action (FE-51). */
  onAction: (suggestion: Suggestion) => void;
  /** Simple mode shows at most two chips (FE-50). */
  max?: number;
}

export function SuggestionChips({ suggestions, onFill, onAction, max = 3 }: Props) {
  const { t } = useT();
  const shown = suggestions.slice(0, max);
  if (shown.length === 0) return null;
  return (
    <div role="group" aria-label={t("chips.label")} className="flex flex-wrap gap-[var(--sp-tap-gap)]">
      {shown.map((chip) => (
        <button
          key={chip.action_id ?? chip.label}
          type="button"
          className="sp-btn sp-btn-secondary min-h-tap"
          onClick={() => (chip.kind === "reply" ? onFill(chip.fill_text ?? chip.label) : onAction(chip))}
        >
          {chip.label}
        </button>
      ))}
    </div>
  );
}
