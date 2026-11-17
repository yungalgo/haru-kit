// Shared characters: the list of the character registry; Add puts a character in the user's library.
import { FC, useCallback, useEffect, useState } from "react";
import { LoaderCircle, TriangleAlert, UserRound } from "lucide-react";
import { Button } from "../../components/Button/Button";
import { useT } from "../../i18n";
import { CharacterFile } from "../../library/library";
import { fetchRegistry, RegistryEntry } from "../../library/registry";

type RegistrySectionProps = {
  url: string;
  // The language that the model speaks: titles and summaries in it, else in the entry's first language.
  language: string;
  // The characters in the user's library.
  library: CharacterFile[];
  disabled: boolean;
  // Downloads and imports the entry; resolves with the error message, or null when it worked.
  onAdd: (entry: RegistryEntry) => Promise<string | null>;
  // The message of an error of the list.
  errorText: (error: unknown) => string;
};

type List = { status: "loading" } | { status: "failed"; error: string } | { status: "ready"; entries: RegistryEntry[] };

const inLanguage = (texts: Record<string, string>, entry: RegistryEntry, language: string) =>
  texts[language] ?? texts[entry.languages[0]] ?? Object.values(texts)[0] ?? "";

export const RegistrySection: FC<RegistrySectionProps> = ({ url, language, library, disabled, onAdd, errorText }) => {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [list, setList] = useState<List>({ status: "loading" });
  // The entry being added, and the last error of each entry.
  const [adding, setAdding] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setList({ status: "loading" });
    setErrors({});
    try {
      setList({ status: "ready", entries: await fetchRegistry(url) });
    } catch (e) {
      setList({ status: "failed", error: errorText(e) });
    }
  }, [url, errorText]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  const add = useCallback(async (entry: RegistryEntry) => {
    setAdding(entry.id);
    const error = await onAdd(entry);
    setErrors(old => ({ ...old, [entry.id]: error ?? "" }));
    setAdding(null);
  }, [onAdd]);

  return (
    <div className="flex flex-col gap-2.5">
      <Button className="self-start" selected={open} onClick={() => setOpen(!open)}>{t("registry.browse")}</Button>
      {open && <div className="flex flex-col gap-2.5 rounded-xl bg-paper p-3.5">
        <p className="text-[13px] font-semibold">{t("registry.title")}</p>
        <p className="text-xs text-muted">{t("registry.note")}</p>
        {list.status === "loading" && <p className="flex items-center gap-1.5 text-xs text-muted">
          <LoaderCircle className="h-3.5 w-3.5 shrink-0 animate-spin" />{t("registry.loading")}
        </p>}
        {list.status === "failed" && <div className="flex flex-wrap items-center gap-2.5">
          <p className="flex items-start gap-1.5 text-xs text-warning">
            <TriangleAlert className="mt-px h-3.5 w-3.5 shrink-0" />{list.error}
          </p>
          <Button onClick={load}>{t("action.retry")}</Button>
        </div>}
        {list.status === "ready" && list.entries.length === 0 && <p className="text-xs text-muted">{t("registry.empty")}</p>}
        {list.status === "ready" && list.entries.map(entry => {
          const mine = library.find(c => c.id === entry.id);
          const state = !mine ? "add" : (mine.version ?? "") === entry.version ? "added" : "update";
          const meta = [
            entry.languages.join(" · ").toUpperCase(),
            entry.author && t("registry.by", { author: entry.author }),
            entry.version && t("character.versionLabel", { version: entry.version }),
            entry.updated && t("character.updatedLabel", { updated: entry.updated }),
          ].filter(Boolean);
          return (
            <div key={entry.id} className="flex items-start gap-3 rounded-xl bg-card p-3">
              {entry.image ?
                <img src={entry.image} alt="" className="h-12 w-12 shrink-0 rounded-full object-cover" /> :
                <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-fill text-muted">
                  <UserRound className="h-6 w-6" />
                </span>}
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="truncate text-sm font-semibold">{inLanguage(entry.titles, entry, language) || entry.id}</span>
                <span className="line-clamp-2 text-xs text-muted">{inLanguage(entry.summaries, entry, language)}</span>
                <span className="text-[11px] text-muted">{meta.join(" · ")}</span>
                {errors[entry.id] && <span className="flex items-start gap-1.5 text-xs text-warning">
                  <TriangleAlert className="mt-px h-3.5 w-3.5 shrink-0" />{errors[entry.id]}
                </span>}
              </span>
              <Button accent={state !== "added"} className="shrink-0" disabled={disabled || state === "added" || adding !== null}
                onClick={() => add(entry)}>
                {adding === entry.id && <LoaderCircle className="h-3.5 w-3.5 animate-spin" />}
                {t(state === "add" ? "registry.add" : state === "added" ? "registry.added" : "registry.update")}
              </Button>
            </div>
          );
        })}
      </div>}
    </div>
  );
};
