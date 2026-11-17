// Characters: picture, library title, summary and author, and the actions on the selected character.
import { FC, ReactNode, useRef } from "react";
import { ImagePlus } from "lucide-react";
import { Button } from "../../components/Button/Button";
import { useT } from "../../i18n";
import { CharacterFile, Version } from "../../library/library";
import { TextField } from "./ui";

type HeaderFieldsProps = {
  version: Version;
  editVersion: (change: Partial<Version>) => void;
  author: string;
  editAuthor: (author: string) => void;
  imageUrl: string;
  setImage: (blob: Blob) => void;
  disabled: boolean;
};

// The picture as a large avatar on the left (click it to choose or replace the picture); the fields on the right.
export const HeaderFields: FC<HeaderFieldsProps> = ({ version, editVersion, author, editAuthor, imageUrl, setImage, disabled }) => {
  const t = useT();
  const fileInput = useRef<HTMLInputElement>(null);
  return (
    <div className="flex items-start gap-4 sm:gap-5">
      <div className="flex w-24 shrink-0 flex-col items-center gap-1.5 sm:w-28">
        <button type="button" disabled={disabled} onClick={() => fileInput.current?.click()}
          aria-label={t("character.pictureHint")} title={t("character.pictureHint")}
          className={`flex h-24 w-24 items-center justify-center overflow-hidden rounded-full bg-fill text-muted transition-shadow hover:ring-2 hover:ring-accent disabled:cursor-not-allowed disabled:opacity-60 sm:h-28 sm:w-28 ${imageUrl ? "" : "border border-dashed border-line"}`}>
          {imageUrl ?
            <img src={imageUrl} alt="" className="h-full w-full object-cover" /> :
            <ImagePlus className="h-8 w-8" />}
        </button>
        {!imageUrl && <span className="text-center text-[11px] leading-tight text-muted">{t("character.pictureHint")}</span>}
        <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp" className="hidden"
          onChange={e => {
            const file = e.target.files?.[0];
            if (file) setImage(file);
            e.target.value = "";
          }} />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        <TextField label={t("character.libraryTitle")} help={t("character.libraryTitleHelp")} value={version.title}
          disabled={disabled} onChange={title => editVersion({ title })} />
        <TextField label={t("character.summary")} help={t("character.summaryHelp")} value={version.summary}
          disabled={disabled} onChange={summary => editVersion({ summary })} />
        <TextField label={t("character.author")} help={t("character.authorHelp")} value={author}
          disabled={disabled} onChange={editAuthor} />
        <p className="text-xs text-muted">{t("character.matchLanguageNote")}</p>
      </div>
    </div>
  );
};

// The character's version and date of the last save, read-only (each only when set).
export const CharacterMeta: FC<{ character: CharacterFile }> = ({ character }) => {
  const t = useT();
  const parts = [
    character.version ? t("character.versionLabel", { version: character.version }) : "",
    character.updated ? t("character.updatedLabel", { updated: character.updated }) : "",
  ].filter(Boolean);
  return parts.length > 0 ? <p className="text-[11px] text-muted">{parts.join(" · ")}</p> : null;
};

type ActionsProps = {
  isNew: boolean;
  unsaved: boolean;
  // The character has a picture (required to save).
  hasPicture: boolean;
  // The character has another language version than the one shown.
  otherVersions: boolean;
  languageLabel: string;
  disabled: boolean;
  onSave: () => void;
  onRevert: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
  onDeleteVersion: () => void;
  // Export and import.
  extra?: ReactNode;
};

export const CharacterActions: FC<ActionsProps> = ({
  isNew, unsaved, hasPicture, otherVersions, languageLabel, disabled, onSave, onRevert, onDuplicate, onDelete, onDeleteVersion, extra,
}) => {
  const t = useT();
  return <>
    <Button accent disabled={disabled || !unsaved || !hasPicture} onClick={onSave}>{t("character.save")}</Button>
    <Button disabled={disabled || !unsaved || isNew} onClick={onRevert}>{t("character.revert")}</Button>
    <Button disabled={disabled || !hasPicture} onClick={onDuplicate}>{t("character.duplicate")}</Button>
    <Button disabled={disabled} onClick={onDelete}>{t("character.delete")}</Button>
    {!isNew && otherVersions &&
      <Button disabled={disabled} onClick={onDeleteVersion}>{t("character.deleteVersion", { language: languageLabel })}</Button>}
    {!hasPicture && <span className="text-xs text-warning">{t("character.pictureRequired")}</span>}
    {extra}
  </>;
};
