// Settings: the same settings, in the same order, as the Mac app.
import { FC, useEffect, useState } from "react";
import { Headphones, TriangleAlert } from "lucide-react";
import { Button, SwitchButton } from "../../components/Button/Button";
import { fieldClass } from "../../components/Input/Input";
import { TurnKey } from "../Conversation/turnKey";
import { useT } from "../../i18n";
import { Card, Hint, SectionTitle, Select, SettingRow } from "./ui";
import { deviceName, Devices, KitInfo, outputSelectable, SLOW_STEP_MS } from "./settings";

type SettingsProps = {
  disabled: boolean;
  devices: Devices;
  microphone: string;
  setMicrophone: (id: string) => void;
  output: string;
  setOutput: (id: string) => void;
  cfgScale: number;
  setCfgScale: (value: number) => void;
  resetCfgScale: () => void;
  kit: KitInfo | null;
  setPrecision: (value: "bf16" | "int8") => void;
  turnKey: TurnKey;
  setTurnKey: (value: TurnKey) => void;
};

export const SettingsSection: FC<SettingsProps> = ({
  disabled, devices, microphone, setMicrophone, output, setOutput, cfgScale, setCfgScale, resetCfgScale,
  kit, setPrecision, turnKey, setTurnKey,
}) => {
  const t = useT();
  // Text of the CFG field, so a half-typed value ("1.") is not overwritten; follows the slider.
  const [cfgInput, setCfgInput] = useState(cfgScale.toString());
  useEffect(() => setCfgInput(cfgScale.toString()), [cfgScale]);
  const hasTurnKey = !!(turnKey.keyId.trim() && turnKey.token.trim());

  // Devices without a name (the page may not use the microphone yet) are numbered.
  const options = (list: MediaDeviceInfo[], label: string): [string, string][] => [
    ["", t("settings.automatic")],
    ...list.filter(d => d.deviceId && d.deviceId !== "default" && d.deviceId !== "communications")
      .map((d, i) => [d.deviceId, d.label || `${label} ${i + 1}`] as [string, string]),
  ];
  // Same rules as the Mac app's deviceWarning.
  const inputName = deviceName(devices.inputs, microphone);
  const outputName = deviceName(devices.outputs, outputSelectable ? output : "");
  const parts: string[] = [];
  if (outputName.includes("Speakers")) parts.push(t("devices.speakers"));
  if (inputName.includes("MacBook")) parts.push(t("devices.microphone"));
  if ((inputName + outputName).includes("AirPods")) parts.push(t("devices.bluetooth"));

  return (
    <Card>
      <SectionTitle title={t("settings.title")} note="" />
      <SettingRow label={t("settings.microphone")}>
        <Select className="min-w-0 flex-1" value={microphone} onChange={setMicrophone} disabled={disabled}
          options={options(devices.inputs, t("settings.microphone"))} />
      </SettingRow>
      <SettingRow label={t("settings.output")}>
        {outputSelectable ?
          <Select className="min-w-0 flex-1" value={output} onChange={setOutput} disabled={disabled}
            options={options(devices.outputs, t("settings.output"))} />
          : <span className="min-w-0 flex-1 text-[13px] text-muted">{t("settings.outputSystem")}</span>}
      </SettingRow>
      <div className="flex items-start gap-2.5 rounded-xl bg-paper p-3.5">
        <Headphones className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
        <div className="flex flex-col gap-1">
          <p className="text-[13px] font-semibold">{t("devices.title")}</p>
          <p className="text-xs text-muted">{t("devices.body")}</p>
          {parts.length > 0 && <p className="flex items-start gap-1.5 text-xs text-warning">
            <TriangleAlert className="mt-px h-3.5 w-3.5 shrink-0" />{t("devices.warning", { parts: parts.join(t("devices.separator")) })}
          </p>}
        </div>
      </div>

      {/* Each control sits between what its two ends do. */}
      <SettingRow label="CFG scale" help={t("settings.cfgHelp")}>
        <div className="flex min-w-0 max-w-full items-center gap-2.5">
          <Hint>{t("settings.cfgLow")}</Hint>
          <input type="range" className="w-[200px] min-w-0" step="0.05" min="1" max="2" disabled={disabled}
            value={cfgScale} onChange={e => setCfgScale(parseFloat(e.target.value))} />
          <Hint>{t("settings.cfgHigh")}</Hint>
        </div>
        <input type="number" className={`${fieldClass} !w-20 !py-1 tabular-nums`} step="0.05" min="1" max="2"
          value={cfgInput} disabled={disabled}
          onChange={e => {
            setCfgInput(e.target.value);
            const v = parseFloat(e.target.value);
            if (Number.isFinite(v) && v >= 1 && v <= 2) setCfgScale(v);
          }}
          onBlur={() => setCfgInput(cfgScale.toString())} />
      </SettingRow>
      <SwitchButton className="self-start" disabled={disabled} onClick={resetCfgScale}>{t("settings.cfgReset")}</SwitchButton>

      {kit?.precision_switch && kit.precision && <SettingRow label={t("settings.precision")} help={t("settings.precisionHelp")}>
        <Hint>{t("settings.accurate")}</Hint>
        {([["bf16", "BF16"], ["int8", "int8"]] as const).map(([value, label]) => (
          <Button key={value} selected={kit.precision === value} disabled={disabled}
            onClick={() => kit.precision !== value && setPrecision(value)}>{label}</Button>
        ))}
        <Hint>{t("settings.faster")}</Hint>
      </SettingRow>}
      {kit?.precision_switch && kit.precision === "bf16" && (kit.step_ms ?? 0) > SLOW_STEP_MS &&
        <p className="flex flex-wrap items-center gap-1.5 text-xs text-warning">
          <TriangleAlert className="h-3.5 w-3.5 shrink-0" />{t("settings.precisionSlow", { ms: kit.step_ms ?? 0 })}
          <Button disabled={disabled} onClick={() => setPrecision("int8")}>{t("settings.useInt8")}</Button>
        </p>}

      <div className="flex flex-col gap-2.5 rounded-xl bg-paper p-3.5">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[13px] font-semibold">{t("webrtc.title")}</p>
          {hasTurnKey && <Button onClick={() => setTurnKey({ keyId: "", token: "" })}>{t("webrtc.clear")}</Button>}
        </div>
        <p className="text-xs text-muted">
          {t("webrtc.help")}
        </p>
        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
          <input type="text" className={fieldClass} value={turnKey.keyId} placeholder="TURN Key ID"
            autoComplete="off" spellCheck={false} disabled={disabled}
            onChange={e => setTurnKey({ ...turnKey, keyId: e.target.value })} />
          <input type="password" className={fieldClass} value={turnKey.token} placeholder="TURN Key API Token"
            autoComplete="off" disabled={disabled}
            onChange={e => setTurnKey({ ...turnKey, token: e.target.value })} />
        </div>
        <p className="text-xs text-muted">
          {t("webrtc.privacy")}
        </p>
      </div>
    </Card>
  );
};
