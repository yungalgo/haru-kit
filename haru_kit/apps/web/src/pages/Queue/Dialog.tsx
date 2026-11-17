// A question with a few answers, over the page.
import { ReactNode, useCallback, useEffect, useState } from "react";
import { Button } from "../../components/Button/Button";

// [answer, label, main action?]
export type Answer = [string, string, boolean?];
// Shows `message`; resolves with the chosen answer, or null when the user closes the question.
export type Ask = (message: string, answers: Answer[]) => Promise<string | null>;

type Question = { message: string; answers: Answer[]; resolve: (answer: string | null) => void };

export const useDialog = (): [ReactNode, Ask] => {
  const [question, setQuestion] = useState<Question | null>(null);
  const ask = useCallback<Ask>((message, answers) => new Promise(resolve => setQuestion({ message, answers, resolve })), []);
  const answer = useCallback((value: string | null) => {
    setQuestion(current => {
      current?.resolve(value);
      return null;
    });
  }, []);
  useEffect(() => {
    if (!question) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && answer(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [question, answer]);
  const element = question &&
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={() => answer(null)}>
      <div role="alertdialog" className="flex w-full max-w-[420px] flex-col gap-4 rounded-[18px] bg-paper p-[22px] shadow-xl"
        onClick={e => e.stopPropagation()}>
        <p className="whitespace-pre-wrap text-sm">{question.message}</p>
        <div className="flex flex-wrap justify-end gap-2">
          {question.answers.map(([value, label, main]) =>
            <Button key={value} accent={main} onClick={() => answer(value)}>{label}</Button>)}
        </div>
      </div>
    </div>;
  return [element, ask];
};
