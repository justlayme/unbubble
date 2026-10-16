import { useMemo } from "react";
import type { CapturePhase } from "../connect/protocol";

export interface CaptureState {
  name: string;
  phase: CapturePhase;
  fraction: number;
  message: string;
  incremental: boolean;
}

const IPHONE_PHASES: { id: CapturePhase; label: string }[] = [
  { id: "trust", label: "Connect to your iPhone" },
  { id: "stream", label: "Read messages" },
  { id: "unpack", label: "Unpack attachments" },
  { id: "rebuild", label: "Rebuild every conversation" },
];

export function Capture({ state, onCancel }: { state: CaptureState; onCancel: () => void }) {
  const phases = IPHONE_PHASES;
  const current = phases.findIndex((p) => p.id === state.phase);
  const pct = Math.max(0, Math.min(100, Math.floor(state.fraction * 100)));

  // Abstract message shapes that condense onto the glass while the phone streams. Not real data.
  const shapes = useMemo(
    () =>
      Array.from({ length: 7 }, (_, i) => ({
        me: (i * 7) % 3 === 0,
        width: 34 + ((i * 37) % 46),
        delay: (i * 0.53) % 7,
        height: i % 4 === 0 ? 46 : 30,
      })),
    [],
  );

  return (
    <main className="stage">
      <section className="lede">
        <h1>{state.incremental ? "Pulling what's new." : "Reading your messages."}</h1>
        <p>
          {state.incremental
            ? "Only messages and attachments that arrived since your last export are coming across."
              : "Keep your iPhone plugged in and unlocked. Only your messages are kept; everything else is skipped as it arrives."}
        </p>
        <ul className="phases" style={{ marginTop: 36 }} aria-label="Progress">
          {phases.map((p, i) => (
            <li key={p.id} data-state={i < current ? "done" : i === current ? "now" : "todo"}>
              {p.label}
            </li>
          ))}
        </ul>
      </section>

      <section className="mirror" data-capturing="" aria-live="polite" aria-busy="true">
        <div className="condense" aria-hidden="true">
          {shapes.map((s, i) => (
            <span
              key={i}
              className={s.me ? "me" : undefined}
              style={{ width: `${s.width}%`, height: s.height, animationDelay: `${s.delay}s` }}
            />
          ))}
        </div>
        <div className="mirror-body">
          <p className="sub">{state.name}</p>
          <div className="percent" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
            {pct}
            <small>%</small>
          </div>
          <div className="track" aria-hidden="true">
            <i style={{ transform: `scaleX(${state.fraction})` }} />
          </div>
          <p className="sub">{state.message}</p>
        </div>
        <div className="foot">
          <button className="btn btn-quiet" onClick={onCancel}>
            Stop export
          </button>
        </div>
      </section>
    </main>
  );
}
