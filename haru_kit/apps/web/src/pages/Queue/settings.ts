// Settings kept in this browser between visits, and the audio devices.
import { useCallback, useEffect, useState } from "react";

// A value kept in localStorage under `key` (JSON).
export const useStored = <T,>(key: string, initial: T): [T, (value: T) => void] => {
  const [value, setValueBase] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw == null ? initial : JSON.parse(raw) as T;
    } catch {
      return initial;
    }
  });
  const setValue = useCallback((next: T) => {
    setValueBase(next);
    try {
      localStorage.setItem(key, JSON.stringify(next));
    } catch {
      // Storage may be unavailable (private mode); the page works without it.
    }
  }, [key]);
  return [value, setValue];
};

// What GET /api/kit tells about the server.
export type KitInfo = {
  precision: "bf16" | "int8" | null;
  precision_switch: boolean;
  ffmpeg: boolean;
  model_failed?: boolean;
  // Mac: the measured model step in ms (a frame is 133 ms). Above SLOW_STEP_MS, BF16 is too slow.
  step_ms?: number | null;
  // The URL of the shared character registry (null: none, hide Browse).
  registry?: string | null;
};

export const SLOW_STEP_MS = 110;

type Sinkable = { setSinkId: (id: string) => Promise<void> };
const canSink = (target: object): target is Sinkable => "setSinkId" in target && typeof target.setSinkId === "function";

// The conversation plays through an AudioContext: the output can be chosen only where it has setSinkId.
export const outputSelectable = canSink(AudioContext.prototype);

// Send audio of `target` to the output `deviceId` ("" = the system output), where the browser can.
export const setSink = async (target: AudioContext | HTMLMediaElement, deviceId: string) => {
  if (outputSelectable && canSink(target)) {
    try {
      await target.setSinkId(deviceId);
    } catch (error) {
      console.error("setSinkId", error);
    }
  }
};

// Microphones and outputs; follows devices that are plugged in or removed.
export type Devices = {
  inputs: MediaDeviceInfo[];
  outputs: MediaDeviceInfo[];
  // Ids are hidden until the page may use the microphone.
  known: boolean;
  refresh: () => Promise<void>;
};

export const useDevices = (): Devices => {
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const refresh = useCallback(async () => {
    try {
      setDevices(await navigator.mediaDevices.enumerateDevices());
    } catch (error) {
      console.error("enumerateDevices", error);
    }
  }, []);
  useEffect(() => {
    refresh();
    navigator.mediaDevices?.addEventListener("devicechange", refresh);
    return () => navigator.mediaDevices?.removeEventListener("devicechange", refresh);
  }, [refresh]);
  return {
    inputs: devices.filter(d => d.kind === "audioinput"),
    outputs: devices.filter(d => d.kind === "audiooutput"),
    known: devices.some(d => d.deviceId !== ""),
    refresh,
  };
};

// The name of the device in use: the chosen one, else the system default.
export const deviceName = (list: MediaDeviceInfo[], chosen: string) =>
  (list.find(d => d.deviceId === (chosen || "default")) ?? list[0])?.label ?? "";
