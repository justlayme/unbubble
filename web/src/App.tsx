import { useCallback, useEffect, useState } from "react";
import mark24 from "./assets/mark-24.png";
import mark48 from "./assets/mark-48.png";
import type { ServerEvent } from "./connect/protocol";
import { HOST } from "./config";
import { explain, type Explanation } from "./copy";
import { Capture, type CaptureState } from "./screens/Capture";
import { Connect } from "./screens/Connect";
import { Library, type UploadPayload } from "./screens/Library";
import { Recording } from "./screens/Recording";
import { getClient, useConnectStatus, useHelperEvents, withTransition } from "./useHelper";

type View =
  | { name: "connect" }
  | { name: "capture"; state: CaptureState; udid?: string }
  | { name: "recording"; resume?: boolean }
  | { name: "library"; archiveId: string };

export interface UnbubbleAppProps {
  /**
   * Optional: offer an upload button on each conversation (a host site's own upload step). Without
   * it, Unbubble is purely an exporter: everything stays on the computer.
   */
  onUpload?: (payload: UploadPayload) => void | Promise<void>;
  /** Label for that button, e.g. "Upload to Grey Mirror". */
  uploadLabel?: string;
  /** Optional: a host site's way back to its own flow (shown as a Back link on the first screen). */
  onBack?: () => void;
  /** Optional: replace the top bar's wordmark (a host site shows its own brand). */
  brand?: { name: string; href?: string };
}

export function UnbubbleApp({ onUpload, uploadLabel = "Upload", onBack, brand }: UnbubbleAppProps) {
  const status = useConnectStatus();
  const [view, setView] = useState<View>({ name: "connect" });
  const [error, setError] = useState<Explanation | null>(null);
  const [passwordFor, setPasswordFor] = useState<string | null>(null);
  const go = (v: View) => withTransition(() => setView(v));

  // Quietly look for the helper on load (never triggers a pairing prompt).
  useEffect(() => {
    if (HOST === "app") document.title = "Unbubble";
    void getClient().detect();
  }, []);

  // A capture already running (e.g. the page was reloaded mid-export): rejoin it.
  useEffect(() => {
    if (status.state !== "connected" || !status.hello.job) return;
    if (status.hello.job.kind === "capture") {
      setView({ name: "capture", state: { name: "Your iPhone", phase: "stream", fraction: 0, message: "Reconnecting to the export in progress", incremental: false } });
    } else if (status.hello.job.kind === "recording") {
      setView({ name: "recording", resume: true });
    }
  }, [status]);

  useHelperEvents(
    useCallback((e: ServerEvent) => {
      switch (e.event) {
        case "capture.phase":
          setView((v) =>
            v.name === "capture"
              ? { ...v, state: { ...v.state, phase: e.data.phase, message: e.data.message, incremental: e.data.incremental ?? v.state.incremental } }
              : v,
          );
          break;
        case "capture.progress":
          setView((v) =>
            v.name === "capture"
              ? { ...v, state: { ...v.state, phase: e.data.phase, fraction: Math.max(v.state.fraction, e.data.fraction), message: e.data.message ?? v.state.message } }
              : v,
          );
          break;
        case "capture.done":
          withTransition(() => setView({ name: "library", archiveId: e.data.archive_id }));
          break;
        case "capture.failed":
          setView((v) => {
            if (e.data.code === "needs_password" && v.name === "capture") setPasswordFor(v.udid ?? null);
            else setError(explain(e.data.code, e.data.hint, e.data.message));
            return { name: "connect" };
          });
          break;
        case "capture.cancelled":
          setView({ name: "connect" });
          break;
      }
    }, []),
  );

  return (
    <div className="gm">
      {view.name !== "library" && (
        <header className="topbar">
          {brand ? (
            <a className="wordmark" href={brand.href ?? "/"}>
              {brand.name}
            </a>
          ) : (
            <span className="wordmark">
              {/* Drawn on whole pixels at 24 and 48; scaling it would blur the grid. */}
              <img className="mark" src={mark24} srcSet={`${mark48} 2x`} width={24} height={24} alt="" />
              Unbubble
            </span>
          )}
          <span className="helper-pill" data-on={status.state === "connected"}>
            <i aria-hidden="true" />
            {status.state === "connected" ? "Unbubble is running" : "Unbubble isn't running"}
          </span>
        </header>
      )}
      {view.name === "connect" && (
        <Connect
          onBack={onBack}
          error={error}
          passwordFor={passwordFor}
          onDismissError={() => setError(null)}
          onNeedPassword={setPasswordFor}
          onCaptureStarted={(info) =>
            go({
              name: "capture",
              udid: info.udid,
              state: { name: info.name, phase: "trust", fraction: 0, message: "Starting", incremental: false },
            })
          }
          onOpenArchive={(id) => go({ name: "library", archiveId: id })}
          onRecording={() => go({ name: "recording" })}
        />
      )}
      {view.name === "recording" && (
        <Recording
          resume={view.resume}
          onBack={() => go({ name: "connect" })}
          onDone={(id) => go({ name: "library", archiveId: id })}
        />
      )}
      {view.name === "capture" && <Capture state={view.state} onCancel={() => void getClient().call("job.cancel", {})} />}
      {view.name === "library" && (
        <Library archiveId={view.archiveId} onBack={() => go({ name: "connect" })} onUpload={onUpload} uploadLabel={uploadLabel} />
      )}
    </div>
  );
}
