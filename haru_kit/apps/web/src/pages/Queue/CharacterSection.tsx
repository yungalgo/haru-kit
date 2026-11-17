// Character: the library and the editor of the selected character's version in the language of the globe menu.
import { FC, ReactNode } from "react";
import { Trash2, TriangleAlert, UserRound } from "lucide-react";
import { Button } from "../../components/Button/Button";
import { useT } from "../../i18n";
import { Character, shownVersion } from "../../library/library";
import { DialogueCard } from "./card";
import { CardEditor } from "./CardEditor";
import { Draft } from "./useCharacters";
import { Card, SectionTitle, Toggle } from "./ui";

type CharacterSectionProps = {
  characters: Character[];
  draft: Draft | null;
  language: string;
  languages: [string, string][];
  onSelect: (character: Character) => void;
  onDelete: (character: Character) => void;
  onlyLanguage: boolean;
  setOnlyLanguage: (value: boolean) => void;
  setField: (key: keyof DialogueCard, value: string) => void;
  rendered: string;
  maxChars: number;
  disabled: boolean;
  unsaved: boolean;
  // Extra tiles, fields above the card and actions below it.
  extraTiles?: ReactNode;
  // The shared characters, next to the library.
  browse?: ReactNode;
  header?: ReactNode;
  actions?: ReactNode;
  // The selected character has no version in the language.
  onWriteVersion: () => void;
  onChooseAnother: () => void;
  // Shown while no character is selected ("" = nothing).
  placeholder: string;
};

const labelOf = (languages: [string, string][], code: string) => languages.find(([c]) => c === code)?.[1] ?? code;

export const CharacterSection: FC<CharacterSectionProps> = ({
  characters, draft, language, languages, onSelect, onDelete, onlyLanguage, setOnlyLanguage, setField,
  rendered, maxChars, disabled, unsaved, extraTiles, browse, header, actions, onWriteVersion, onChooseAnother, placeholder,
}) => {
  const t = useT();
  const languageLabel = labelOf(languages, language);
  const shown = onlyLanguage ? characters.filter(c => c.versions[language]) : characters;
  return (
    <Card>
      <SectionTitle title={t("character.title")} note={t("character.note")} />
      <div className="flex flex-col gap-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-[15px] font-semibold">{t("library.title")}</h3>
          <Toggle label={t("library.onlyLanguage", { language: languageLabel })} checked={onlyLanguage}
            onChange={setOnlyLanguage} />
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {shown.map(c => {
            const [, version] = shownVersion(c, language) ?? ["", null];
            const selected = draft?.base.id === c.id;
            return (
              <div key={c.id} className="relative">
              <button type="button" disabled={disabled} onClick={() => !selected && onSelect(c)}
                className={`flex w-full items-start gap-3 rounded-xl bg-paper p-3 pr-10 text-left transition-shadow disabled:cursor-not-allowed disabled:opacity-60 ${selected ? "ring-2 ring-accent" : "hover:ring-1 hover:ring-line"}`}>
                {c.imageUrl ?
                  <img src={c.imageUrl} alt="" className="h-12 w-12 shrink-0 rounded-full object-cover" /> :
                  <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-fill text-muted">
                    <UserRound className="h-6 w-6" />
                  </span>}
                <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="min-w-0 truncate text-sm font-semibold">{version?.title || t("library.untitled")}</span>
                  {version?.summary && <span className="line-clamp-2 text-xs text-muted">{version.summary}</span>}
                  <span className="text-[11px] uppercase tracking-wide text-muted">{Object.keys(c.versions).join(" · ")}</span>
                </span>
              </button>
              <button type="button" disabled={disabled} onClick={() => onDelete(c)}
                aria-label={t("character.delete")} title={t("character.delete")}
                className="absolute right-1.5 top-1.5 flex h-7 w-7 items-center justify-center rounded-full text-muted hover:bg-fill hover:text-ink disabled:opacity-40">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
              </div>
            );
          })}
          {extraTiles}
        </div>
        {shown.length === 0 && <p className="text-[13px] text-muted">{t("library.empty", { language: languageLabel })}</p>}
        {browse}
      </div>

      {draft?.missing ?
        <div className="flex flex-col gap-3 rounded-xl bg-paper p-3.5">
          <p className="flex items-start gap-1.5 text-[13px]">
            <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
            {t("character.missingVersion", { language: languageLabel })}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button accent disabled={disabled} onClick={onWriteVersion}>{t("character.writeVersion", { language: languageLabel })}</Button>
            <Button disabled={disabled} onClick={onChooseAnother}>{t("character.chooseAnother")}</Button>
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
        : draft ? <>
          {draft.newVersion && <p className="text-xs text-accent">{t("character.newVersionNote", { language: languageLabel })}</p>}
          {header}
          <CardEditor card={draft.version.card} onChange={setField} rendered={rendered} maxChars={maxChars} disabled={disabled} />
          <div className="flex flex-wrap items-center gap-2">
            {actions}
            {unsaved && <span className="ml-auto text-xs text-warning">{t("character.unsaved")}</span>}
          </div>
        </> : placeholder && <p className="text-[13px] text-muted">{placeholder}</p>}
    </Card>
  );
};
