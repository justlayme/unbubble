import { useCallback, useEffect, useState } from "react";
import { prefersRelay } from "../connect/client";
import { HelperError, type ArchiveInfo, type Device, type Hello, type ServerEvent } from "../connect/protocol";
import { HOST, SOURCE_URL, WINDOWS_DOWNLOAD } from "../config";
import { explain, type Explanation } from "../copy";
import { ago, count, fmtShort, span } from "../format";
import { getClient, useConnectStatus, useHelperEvents, usePoll } from "../useHelper";
import { Notice } from "../components/Notice";
import { PasswordDialog } from "../components/PasswordDialog";

interface Props {
  error: Explanation | null;
  passwordFor: string | null;
  onDismissError: () => void;
  onCaptureStarted: (info: { name: string; udid: string }) => void;
  onOpenArchive: (id: string) => void;
  onNeedPassword: (udid: string | null) => void;
  onRecording: () => void;
  onBack?: () => void;
}

type DriverState = { phase: "idle" } | { phase: "working"; message: string } | { phase: "failed"; explanation: Explanation };

export function Connect({ error, passwordFor, onDismissError, onCaptureStarted, onOpenArchive, onNeedPassword, onRecording, onBack }: Props) {
  const status = useConnectStatus();
  const connected = status.state === "connected";
  const hello: Hello | null = connected ? status.hello : null;
  const client = getClient();

  const [driverMissing, setDriverMissing] = useState(false);
  const [driver, setDriver] = useState<DriverState>({ phase: "idle" });
  const [deviceError, setDeviceError] = useState<Explanation | null>(null);
  const [archives, setArchives] = useState<ArchiveInfo[]>([]);
  const [starting, setStarting] = useState(false);

  // Poll for iPhones. A missing Apple driver comes back as a specific error, which drives the driver step.
  const listDevices = useCallback(async () => {
    try {
      const d = await client.call("devices.list", {});
      setDriverMissing(false);
      setDeviceError(null);
      return d;
    } catch (e) {
      if (e instanceof HelperError && e.data.hint === "windows_drivers") setDriverMissing(true);
      else if (e instanceof HelperError) setDeviceError(explain(e.code, e.data.hint, e.message));
      return [];
    }
  }, [client]);
  const devices = usePoll<Device[]>(listDevices, 2000, connected && driver.phase !== "working");

  useEffect(() => {
    if (!connected) return;
    client.call("archives.list", {}).then(setArchives, () => {});
  }, [client, connected]);

  useHelperEvents(
    useCallback((e: ServerEvent) => {
      if (e.event === "drivers.progress") setDriver({ phase: "working", message: e.data.message });
    }, []),
  );

  async function installDriver() {
    setDriver({ phase: "working", message: "Preparing" });
    try {
      const r = await client.call("drivers.install", {}, 15 * 60_000);
      if (r.ok) {
        setDriverMissing(false);
        setDriver({ phase: "idle" });
      } else {
        setDriver({ phase: "failed", explanation: explain(r.reason === "declined" ? "driver_declined" : "driver_failed") });
      }
    } catch (e) {
      setDriver({ phase: "failed", explanation: explain(e instanceof HelperError ? e.code : "driver_failed") });
    }
  }

  const device = devices?.[0];

  async function startIphone(d: Device, password?: string) {
    setStarting(true);
    onDismissError();
    try {
      await client.call("capture.start", { udid: d.udid, ...(password ? { password } : {}) });
      onCaptureStarted({ name: d.name ?? "iPhone", udid: d.udid });
    } catch (e) {
      if (e instanceof HelperError && e.code === "needs_password") onNeedPassword(d.udid);
      else if (e instanceof HelperError) setDeviceError(explain(e.code, e.data.hint, e.message));
    } finally {
      setStarting(false);
    }
  }

  async function removeArchive(a: ArchiveInfo) {
    if (!confirm(`Delete the export of ${a.device_name ?? a.label} from ${fmtShort(a.created)}? Files you saved to other folders aren't affected.`)) return;
    await client.call("archive.delete", { archive_id: a.id });
    setArchives((list) => list.filter((x) => x.id !== a.id));
  }

  const steps = [
    { label: HOST === "app" ? "Open Unbubble" : "Install Unbubble", detail: "Free and open source. It also sets up Apple's iPhone driver.", done: connected && !driverMissing },
    { label: "Plug in your iPhone", detail: "Any Lightning or USB‑C cable that can carry data.", done: Boolean(device) },
    { label: "Unlock it and tap Trust", detail: "Your iPhone may also ask for its passcode once the export starts.", done: device?.state === "ready" },
  ];
  const nowIndex = steps.findIndex((s) => !s.done);
  const shownError = error ?? deviceError;

  return (
    <>
      <main className="stage">
        <section className="lede">
          {onBack && (
            <button className="btn-link back-link" onClick={onBack}>
              Back
            </button>
          )}
          <h1>Plug in your iPhone.</h1>
          <p>
            Unbubble copies every iMessage and text conversation from your iPhone to this PC, with photos,
            reactions and edits. Only your messages are copied, so there's no full backup.
          </p>
          <ol className="steps" aria-label="Steps">
            {steps.map((s, i) => (
              <li key={s.label} data-state={s.done ? "done" : i === nowIndex ? "now" : "todo"}>
                <div>
                  {s.label}
                  <small>{s.detail}</small>
                </div>
              </li>
            ))}
          </ol>
          {connected && (
            <div className="alt-route">
              <p>
                <b>Rather not plug in?</b> Screen-record a conversation on your iPhone while you scroll it, then drop the
                video here. Unbubble turns it into the same export.
              </p>
              <button className="btn btn-quiet" onClick={onRecording}>
                Use a screen recording
              </button>
            </div>
          )}
        </section>

        <section className="mirror" aria-live="polite">
          <div className="mirror-body">
            {!connected && <HelperSetup />}

            {connected && driverMissing && (
              <>
                <div>
                  <h2>One more thing: Apple's iPhone driver</h2>
                  <p className="sub" style={{ marginTop: 10 }}>
                    Windows can only talk to an iPhone through Apple's free driver. Unbubble installs it
                    straight from Apple; Windows will ask for permission.
                  </p>
                </div>
                {driver.phase === "working" ? (
                  <p className="sub" role="status">
                    {driver.message}…
                  </p>
                ) : (
                  <div className="actions">
                    <button className="btn btn-primary" onClick={installDriver}>
                      Install iPhone driver
                    </button>
                  </div>
                )}
                {driver.phase === "failed" && <Notice explanation={driver.explanation} onInstallDriver={installDriver} />}
              </>
            )}

            {connected && hello && !driverMissing && (
              <>
                <div className="phone" data-state={device ? "present" : "absent"} aria-hidden="true">
                  <span className="cable" />
                </div>
                {!device && (
                  <>
                    <h2>Plug in your iPhone</h2>
                    <p className="sub">Unbubble is running and watching for it.</p>
                  </>
                )}
                {device && (
                  <>
                    <div>
                      <h2>{device.name ?? "Your iPhone"}</h2>
                      <p className="sub">
                        {device.state === "ready"
                          ? [device.model, device.ios && `iOS ${device.ios}`].filter(Boolean).join(", ")
                          : device.state === "locked"
                            ? "Unlock your iPhone to continue."
                            : "Unlock your iPhone and tap Trust."}
                      </p>
                    </div>
                    <div className="actions">
                      <button className="btn btn-primary" disabled={starting} onClick={() => startIphone(device)}>
                        {starting ? "Starting…" : device.synced ? "Update export" : "Export messages"}
                      </button>
                    </div>
                  </>
                )}
                {shownError && (
                  <Notice
                    explanation={shownError}
                    onRetry={() => {
                      setDeviceError(null);
                      onDismissError();
                    }}
                  />
                )}
              </>
            )}
          </div>
          {connected && device && !driverMissing && (
            <div className="foot">
              {device.synced
                ? `Last export ${ago(device.last_sync)}. Updates only pull messages that are new since then.`
                : "The first export takes about as long as an iPhone backup. After that, only new messages are pulled."}
            </div>
          )}
        </section>
      </main>

      {connected && archives.length > 0 && (
        <section className="history" aria-labelledby="history-title">
          <h3 id="history-title">Your exports</h3>
          <ul>
            {archives.map((a) => (
              <li key={a.id}>
                <div>
                  <div>{a.source === "screen_recording" ? a.label : a.device_name ?? a.label}</div>
                  <div className="meta">
                    {fmtShort(a.created)}, {count(a.messages, "message")}, {count(a.threads, "conversation")}
                    {a.first_ts ? `, ${span(a.first_ts, a.last_ts)}` : ""}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <button className="btn btn-quiet" onClick={() => removeArchive(a)} aria-label={`Delete export from ${fmtShort(a.created)}`}>
                    Delete
                  </button>
                  <button className="btn btn-primary" onClick={() => onOpenArchive(a.id)}>
                    Open
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      <PasswordDialog
        open={passwordFor !== null}
        onCancel={() => onNeedPassword(null)}
        onSubmit={(pw) => {
          const d = devices?.find((x) => x.udid === passwordFor) ?? device;
          onNeedPassword(null);
          if (d) void startIphone(d, pw);
        }}
      />
      <footer className="history" style={{ paddingBottom: 32, color: "var(--graphite)", fontSize: 13 }}>
        Unbubble is free and open source.{" "}
        <a href={SOURCE_URL} target="_blank" rel="noreferrer">
          Read the code on GitHub
        </a>
        .
      </footer>
    </>
  );
}

function HelperSetup() {
  const status = useConnectStatus();
  const client = getClient();

  if (status.state === "searching") {
    return (
      <>
        <h2>Looking for Unbubble…</h2>
        <p className="sub">If your browser asks to open it, choose Open.</p>
      </>
    );
  }
  if (status.state === "awaiting_approval") {
    return (
      <>
        <p className="sub">Check that your PC shows this code, then choose Yes.</p>
        <div className="code" aria-label={`Pairing code ${status.code.split("").join(" ")}`}>{status.code}</div>
        <p className="sub">This only happens the first time this browser connects.</p>
      </>
    );
  }
  return (
    <>
      <div>
        <h2>{status.state === "lost" ? "Unbubble stopped" : "Get Unbubble"}</h2>
        <p className="sub" style={{ marginTop: 10 }}>
          A small, free, open-source app for Windows that reads your iPhone over the cable. No account, and your messages
          stay on your PC until you choose to upload.
        </p>
      </div>
      {status.state === "denied" && (
        <Notice explanation={{ title: "Connection not allowed", body: "Your PC declined the connection. Connect again and choose Yes." }} />
      )}
      <div className="actions">
        <a className="btn btn-primary" href={WINDOWS_DOWNLOAD}>
          Download for Windows
        </a>
        <button className="btn btn-quiet" onClick={() => void client.connect()}>
          {status.state === "lost" ? "Reconnect" : "It's installed, connect"}
        </button>
      </div>
      <p className="sub">
        Windows 10 and 11.{" "}
        {prefersRelay()
          ? "Your PC will ask you to confirm a code once."
          : "Your browser may ask to let this site connect to apps on your device. Choose Allow."}
      </p>
    </>
  );
}
