import { FC, useEffect, useRef, useState } from "react";
import { Check, Globe } from "lucide-react";
import { LANGUAGES, useLanguage, useT } from "../../i18n";

// The globe menu: the one language of the app's text and of Haru (the card, the voice, the library).
// `onChoose` switches it (it can ask first, when the editor has unsaved changes); `disabled` while a conversation starts.
export const LanguageMenu: FC<{ onChoose: (code: string) => void; disabled?: boolean }> = ({ onChoose, disabled }) => {
  const t = useT();
  const current = useLanguage();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button type="button" title={t("app.languageHelp")} aria-label={t("app.language")} aria-haspopup="menu" aria-expanded={open}
        disabled={disabled}
        className="flex h-8 w-8 items-center justify-center rounded-full text-muted transition-opacity hover:bg-fill hover:text-ink active:opacity-75 disabled:cursor-not-allowed disabled:opacity-60"
        onClick={() => setOpen(!open)}>
        <Globe className="h-4 w-4" />
      </button>
      {open &&
        <div role="menu" aria-label={t("app.language")}
          className="absolute right-0 top-full z-40 mt-1 flex min-w-[160px] flex-col rounded-xl bg-paper py-1 shadow-xl ring-1 ring-line">
          {LANGUAGES.map(([code, name]) =>
            <button key={code} type="button" role="menuitemradio" aria-checked={code === current} lang={code}
              className="flex items-center gap-2 px-3 py-1.5 text-left text-[13px] text-ink hover:bg-fill"
              onClick={() => {
                onChoose(code);
                setOpen(false);
              }}>
              <Check className={`h-3.5 w-3.5 shrink-0 text-accent ${code === current ? "" : "invisible"}`} />{name}
            </button>)}
        </div>}
    </div>
  );
};
