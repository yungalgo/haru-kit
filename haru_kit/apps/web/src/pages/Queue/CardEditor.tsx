// The 7 card fields, each with its help, and the rendered card below, read-only.
import { FC } from "react";
import { useT } from "../../i18n";
import { DialogueCard } from "./card";
import { AreaField, SelectField, TextField } from "./ui";

type CardEditorProps = {
  card: DialogueCard;
  onChange: (key: keyof DialogueCard, value: string) => void;
  // Rendered card text (what the model reads) and its limit.
  rendered: string;
  maxChars: number;
  disabled?: boolean;
};

export const CardEditor: FC<CardEditorProps> = ({ card, onChange, rendered, maxChars, disabled }) => {
  const t = useT();
  const tooLong = rendered.length > maxChars;
  return <>
    <div className="grid grid-cols-1 gap-x-3.5 gap-y-4 sm:grid-cols-2">
      <TextField label={t("field.modelName")} help={t("help.name")} value={card.name} disabled={disabled}
        onChange={v => onChange("name", v)} />
      <TextField label={t("field.yourName")} help={t("help.other_name")} value={card.other_name} disabled={disabled}
        onChange={v => onChange("other_name", v)} />
      <SelectField label={t("field.gender")} help={t("help.gender")} value={card.gender} disabled={disabled}
        onChange={v => onChange("gender", v)}
        options={[["", t("option.unspecified")], ["female", t("option.female")], ["male", t("option.male")]]} />
      <SelectField label={t("field.backchannel")} help={t("help.backchannel")} value={card.backchannel} disabled={disabled}
        onChange={v => onChange("backchannel", v)}
        options={[["", t("option.unspecified")], ["low", t("backchannel.low")], ["mid", t("backchannel.mid")], ["high", t("backchannel.high")]]} />
    </div>
    <AreaField label={t("field.persona")} help={t("help.persona")} rows={5} value={card.persona} disabled={disabled}
      onChange={v => onChange("persona", v)} />
    <AreaField label={t("field.relationship")} help={t("help.relationship")} rows={3} value={card.relationship} disabled={disabled}
      onChange={v => onChange("relationship", v)} />
    <AreaField label={t("field.situation")} help={t("help.situation")} rows={3} value={card.situation} disabled={disabled}
      onChange={v => onChange("situation", v)} />
    <p className="text-xs text-muted">{t("character.hint")}</p>
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-muted">{t("prompt.rendered")}</span>
      <pre className="max-h-72 overflow-y-auto whitespace-pre-wrap rounded-xl bg-paper p-3.5 font-[inherit] text-xs leading-relaxed">{rendered}</pre>
      <p className={`text-right text-xs tabular-nums ${tooLong ? "text-warning" : "text-muted"}`}>
        {t("prompt.length", { count: rendered.length, max: maxChars })}
        {tooLong && <> · {t("error.prompt_too_long", { max_chars: maxChars })}</>}
      </p>
    </div>
  </>;
};
