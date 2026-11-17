// Building blocks of the setup page.
import { FC, ReactNode } from "react";
import { ChevronsUpDown } from "lucide-react";
import { fieldClass } from "../../components/Input/Input";

// A grouped section: soft grey, rounded, like Settings.
export const Card: FC<{ children: ReactNode }> = ({ children }) => (
  <section className="flex flex-col gap-4 rounded-[18px] bg-card p-[22px]">{children}</section>
);

export const SectionTitle: FC<{ title: string, note: string }> = ({ title, note }) => (
  <div className="flex flex-col gap-[3px]">
    <h2 className="text-xl font-semibold">{title}</h2>
    {note && <p className="text-[13px] text-muted">{note}</p>}
  </div>
);

export const Hint: FC<{ children: ReactNode }> = ({ children }) => <span className="text-xs text-muted">{children}</span>;

export const SettingRow: FC<{ label: string, help?: string, children: ReactNode }> = ({ label, help, children }) => (
  <div className="flex flex-col gap-1">
    <div className="flex flex-wrap items-center gap-2.5">
      <span className="w-[84px] shrink-0 text-[13px] font-medium">{label}</span>
      {children}
    </div>
    {help && <p className="text-xs text-muted sm:pl-[94px]">{help}</p>}
  </div>
);

export const FieldBox: FC<{ label: string, help?: string, children: ReactNode }> = ({ label, help, children }) => (
  <label className="flex flex-col gap-1.5">
    <span className="text-xs font-medium text-muted">{label}</span>
    {children}
    {help && <span className="text-xs text-muted">{help}</span>}
  </label>
);

type FieldProps = { label: string, value: string, onChange: (v: string) => void, help?: string, disabled?: boolean };

export const TextField: FC<FieldProps & { placeholder?: string }> = ({ label, value, onChange, placeholder, help, disabled }) => (
  <FieldBox label={label} help={help}>
    <input type="text" className={fieldClass} value={value} placeholder={placeholder} disabled={disabled}
      onChange={e => onChange(e.target.value)} />
  </FieldBox>
);

export const AreaField: FC<FieldProps & { rows: number }> = ({ label, value, onChange, rows, help, disabled }) => (
  <FieldBox label={label} help={help}>
    <textarea className={`${fieldClass} leading-relaxed`} rows={rows} value={value} disabled={disabled}
      onChange={e => onChange(e.target.value)} />
  </FieldBox>
);

export const Select: FC<{ value: string, onChange: (v: string) => void, options: [string, string][], disabled?: boolean, className?: string }> = ({ value, onChange, options, disabled, className }) => (
  <span className={`relative block ${className ?? ""}`}>
    <select className={`${fieldClass} appearance-none pr-9`} value={value} disabled={disabled} onChange={e => onChange(e.target.value)}>
      {options.map(([v, text]) => <option key={v} value={v}>{text}</option>)}
    </select>
    <ChevronsUpDown className="pointer-events-none absolute right-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted" />
  </span>
);

export const SelectField: FC<FieldProps & { options: [string, string][] }> = ({ label, help, ...props }) => (
  <FieldBox label={label} help={help}>
    <Select {...props} />
  </FieldBox>
);

// An on/off switch with its label.
export const Toggle: FC<{ label: string, checked: boolean, onChange: (v: boolean) => void, disabled?: boolean }> = ({ label, checked, onChange, disabled }) => (
  <button type="button" role="switch" aria-checked={checked} disabled={disabled} onClick={() => onChange(!checked)}
    className="inline-flex items-center gap-2.5 text-[13px] font-medium disabled:cursor-not-allowed disabled:opacity-40">
    <span className={`relative h-[22px] w-[38px] shrink-0 rounded-full transition-colors ${checked ? "bg-accent" : "bg-line"}`}>
      <span className={`absolute top-[2px] h-[18px] w-[18px] rounded-full bg-white shadow transition-all ${checked ? "left-[18px]" : "left-[2px]"}`} />
    </span>
    {label}
  </button>
);
