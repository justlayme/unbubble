import { useCallback, useEffect, useRef, useState } from "react";
import { HelperError, type ExportFormat, type ServerEvent, type Thread } from "../connect/protocol";
import { explain, type Explanation } from "../copy";
import { getClient, useConnectStatus, useHelperEvents } from "../useHelper";
import { Notice } from "./Notice";

const FORMATS: { id: ExportFormat; label: string; detail: string }[] = [
  { id: "html", label: "Browsable archive", detail: "Opens in any browser, offline, and looks like your conversations." },
  { id: "pdf", label: "PDF for each conversation", detail: "For printing or records. Dates, reactions and edits are kept." },
  { id: "csv", label: "Spreadsheet (CSV)", detail: "One row per message, for Excel, Numbers or Sheets." },
  { id: "json", label: "Data (JSON)", detail: "Everything, structured, for developers and other tools." },
];

interface Props {
  open: boolean;
  archiveId: string;
  current: Thread | null;
  totalThreads: number;
  onClose: () => void;
}

type Phase = { name: "choose" } | { name: "running"; fraction: number; message: string } | { name: "done"; path: string };

export function ExportDialog({ open, archiveId, current, totalThreads, onClose }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const status = useConnectStatus();
  const platform = status.state === "connected" ? status.hello.platform : "mac";
  const [formats, setFormats] = useState<Set<ExportFormat>>(new Set(["html", "pdf"]));
  const [scope, setScope] = useState<"all" | "current">("all");
  const [withAttachments, setWithAttachments] = useState(true);
  const [dest, setDest] = useState<{ path: string; cloud: boolean } | null>(null);
  const defaultDir = status.state === "connected" ? status.hello.export_dir : "";
  const [phase, setPhase] = useState<Phase>({ name: "choose" });
  const [error, setError] = useState<Explanation | null>(null);

  useEffect(() => {
    if (!open || dest || !defaultDir) return;
    getClient().call("export.check_folder", { path: defaultDir }).then(
      (r) => setDest({ path: r.path, cloud: r.cloud_synced }),
      () => setDest({ path: defaultDir, cloud: false }),
    );
  }, [open, dest, defaultDir]);

  async function chooseFolder() {
    const r = await getClient().call("export.choose_folder", { initial: dest?.path }, 30 * 60_000);
    if (r.path) setDest({ path: r.path, cloud: Boolean(r.cloud_synced) });
  }

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      setPhase((p) => (p.name === "running" ? p : { name: "choose" }));
      setError(null);
      d.showModal();
    } else if (!open && d.open) d.close();
  }, [open]);

  useHelperEvents(
    useCallback((e: ServerEvent) => {
      if (e.event === "export.progress") setPhase({ name: "running", fraction: e.data.fraction, message: e.data.message });
      else if (e.event === "export.saved") setPhase({ name: "done", path: e.data.path });
      else if (e.event === "export.failed") {
        setPhase({ name: "choose" });
        setError(explain(e.data.code, e.data.hint, e.data.message));
      }
    }, []),
  );

  async function start() {
    setError(null);
    try {
      await getClient().call("export.start", {
        archive_id: archiveId,
        formats: [...formats],
        include_attachments: withAttachments,
        ...(dest ? { destination: dest.path } : {}),
        ...(scope === "current" && current ? { thread_ids: [current.id] } : {}),
      });
      setPhase({ name: "running", fraction: 0, message: "Preparing" });
    } catch (e) {
      if (e instanceof HelperError) setError(explain(e.code, e.data.hint, e.message));
    }
  }

  const toggle = (f: ExportFormat) =>
    setFormats((s) => {
      const n = new Set(s);
      if (n.has(f)) n.delete(f);
      else n.add(f);
      return n;
    });

  const folderWord = platform === "windows" ? "File Explorer" : platform === "mac" ? "Finder" : "your files";

  return (
    <dialog ref={ref} className="sheet" onClose={onClose} aria-labelledby="export-title">
      <div className="sheet-body">
        {phase.name === "choose" && (
          <>
            <h2 id="export-title">Save to this computer</h2>
            <fieldset className="choices">
              <legend>Formats</legend>
              {FORMATS.map((f) => (
                <label key={f.id} className="choice">
                  <input type="checkbox" checked={formats.has(f.id)} onChange={() => toggle(f.id)} />
                  <div>
                    <span>{f.label}</span>
                    <small>{f.detail}</small>
                  </div>
                </label>
              ))}
            </fieldset>
            <fieldset className="choices">
              <legend>Conversations</legend>
              <label className="choice">
                <input type="radio" name="scope" checked={scope === "all"} onChange={() => setScope("all")} />
                <div>
                  <span>All {totalThreads.toLocaleString()} conversations</span>
                </div>
              </label>
              {current && (
                <label className="choice">
                  <input type="radio" name="scope" checked={scope === "current"} onChange={() => setScope("current")} />
                  <div>
                    <span>Only {current.title}</span>
                  </div>
                </label>
              )}
            </fieldset>
            <label className="choice">
              <input type="checkbox" checked={withAttachments} onChange={(e) => setWithAttachments(e.target.checked)} />
              <div>
                <span>Include photos and attachments</span>
                <small>Original files, saved next to the export.</small>
              </div>
            </label>
            <div className="dest">
              <div>
                <span className="dest-label">Save to</span>
                <span className="path">{dest?.path ?? defaultDir}</span>
              </div>
              <button className="btn btn-quiet" onClick={chooseFolder}>
                Change…
              </button>
            </div>
            {dest?.cloud && (
              <p className="notice" role="note">
                <strong>This folder syncs to OneDrive</strong>
                Your messages would be uploaded to the cloud. Choose a folder outside OneDrive to keep them only on this PC.
              </p>
            )}
            {error && <Notice explanation={error} />}
            <div className="sheet-actions">
              <button className="btn btn-quiet" onClick={() => ref.current?.close()}>
                Cancel
              </button>
              <button className="btn btn-primary" disabled={formats.size === 0} onClick={start}>
                Save
              </button>
            </div>
          </>
        )}
        {phase.name === "running" && (
          <>
            <h2 id="export-title">Saving…</h2>
            <div className="percent" style={{ fontSize: 72 }} role="progressbar" aria-valuenow={Math.floor(phase.fraction * 100)}>
              {Math.floor(phase.fraction * 100)}
              <small>%</small>
            </div>
            <div className="track">
              <i style={{ transform: `scaleX(${phase.fraction})` }} />
            </div>
            <p>{phase.message}</p>
            <div className="sheet-actions">
              <button className="btn btn-quiet" onClick={() => ref.current?.close()}>
                Keep browsing
              </button>
            </div>
          </>
        )}
        {phase.name === "done" && (
          <>
            <h2 id="export-title">Saved</h2>
            <p className="path">{phase.path}</p>
            <div className="sheet-actions">
              <button className="btn btn-quiet" onClick={() => ref.current?.close()}>
                Done
              </button>
              <button className="btn btn-primary" onClick={() => void getClient().call("export.reveal", { path: phase.path })}>
                Show in {folderWord}
              </button>
            </div>
          </>
        )}
      </div>
    </dialog>
  );
}
