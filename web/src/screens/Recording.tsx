import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  HelperError,
  type Device,
  type DeviceVideo,
  type RecordingInfo,
  type ReviewItem,
  type ServerEvent,
} from "../connect/protocol";
import { explain, type Explanation } from "../copy";
import { ago, bytes, count } from "../format";
import { Notice } from "../components/Notice";
import { getClient, useConnectStatus, useHelperEvents, usePoll, withTransition } from "../useHelper";

/**
 * Turn an iPhone screen recording of a Messages thread into a conversation.
 *
 * iOS doesn't let any app scroll Messages for you, so the person records while they scroll; the
 * video is read on this PC (nothing is uploaded anywhere) and saved like any other export, so the
 * library, search, CSV/HTML/PDF/JSON and the upload all work the same.
 */

type Feed = { id: number; text: string; from_me: boolean }[];

type Step =
  | { name: "choose" }
  | { name: "uploading"; label: string; sent: number; total: number }
  | { name: "reading"; fraction: number; message: string; fromPhone: boolean; label: string; feed: Feed; found: number; eta: number | null }
  | { name: "review"; archiveId: string; info: RecordingInfo; items: ReviewItem[] }
  | { name: "confirm"; archiveId: string; info: RecordingInfo };

interface Props {
  onBack: () => void;
  onDone: (archiveId: string) => void;
  /** A recording already running when the page loaded (rejoin it). */
  resume?: boolean;
  /** A video dropped onto the page. */
  incoming?: File | null;
  onIncomingTaken?: () => void;
}

const HOW_TO = [
  { label: "Open the conversation on your iPhone", detail: "Start at the newest message." },
  { label: "Start a screen recording", detail: "Control Center, then the record button. Add it in Settings › Control Center if it's missing." },
  { label: "Scroll back to where it should start", detail: "Flick, pause, flick. Each pause gives Unbubble a sharp look at that screen." },
  { label: "Stop, then bring it here", detail: "Plug in your iPhone to pick it from Photos, or drop the video on this page." },
];

let feedId = 0;

export function Recording({ onBack, onDone, resume, incoming, onIncomingTaken }: Props) {
  const client = getClient();
  const [step, setStep] = useState<Step>(
    resume
      ? { name: "reading", fraction: 0, message: "Reconnecting to the recording in progress", fromPhone: false, label: "Your recording", feed: [], found: 0, eta: null }
      : { name: "choose" },
  );
  const [error, setError] = useState<Explanation | null>(null);
  const go = (s: Step) => withTransition(() => setStep(s));
  const fail = (e: unknown) =>
    setError(explain(e instanceof HelperError ? e.code : "recording", undefined, e instanceof Error ? e.message : undefined));

  const finish = useCallback(async (archiveId: string) => {
    try {
      const c = getClient();
      const info = await c.call("recording.info", { archive_id: archiveId });
      const items = info.unsure > 0 ? await c.call("recording.review", { archive_id: archiveId }, 60_000) : [];
      withTransition(() => setStep(items.length ? { name: "review", archiveId, info, items } : { name: "confirm", archiveId, info }));
    } catch {
      onDone(archiveId);
    }
  }, [onDone]);

  useHelperEvents(
    useCallback((e: ServerEvent) => {
      switch (e.event) {
        case "recording.progress":
          setStep((s) => (s.name === "reading" ? { ...s, fraction: Math.max(s.fraction, e.data.fraction), message: e.data.message } : s));
          break;
        case "recording.event":
          setStep((s) => {
            if (s.name !== "reading") return s;
            if (e.data.type === "eta") return { ...s, eta: e.data.seconds };
            const fresh = e.data.messages.map((m) => ({ ...m, id: ++feedId }));
            // Reading goes back in time, so what's found now is older: it joins at the top.
            return { ...s, found: e.data.found, feed: [...fresh, ...s.feed].slice(0, 9) };
          });
          break;
        case "recording.done":
          void finish(e.data.archive_id);
          break;
        case "recording.failed":
          setError(explain(e.data.code, e.data.hint, e.data.message));
          withTransition(() => setStep({ name: "choose" }));
          break;
        case "recording.cancelled":
          withTransition(() => setStep({ name: "choose" }));
          break;
      }
    }, [finish]),
  );

  async function start(params: { path?: string; udid?: string; device_path?: string; upload_id?: string }, label: string) {
    setError(null);
    try {
      await client.call("recording.start", params);
      go({ name: "reading", fraction: 0, message: "Opening the video", fromPhone: Boolean(params.udid), label, feed: [], found: 0, eta: null });
    } catch (e) {
      fail(e);
      go({ name: "choose" });
    }
  }

  async function choose() {
    setError(null);
    try {
      const r = await client.call("recording.choose", {}, 15 * 60_000);
      if (r.path) await start({ path: r.path }, r.name ?? "Your recording");
    } catch (e) {
      fail(e);
    }
  }

  const upload = useCallback(async (file: File) => {
    setError(null);
    if (!/\.(mp4|mov|m4v)$/i.test(file.name)) {
      setError({ title: "That isn't a screen recording", body: "Drop the video itself: iPhone screen recordings are .mp4 or .mov files." });
      return;
    }
    try {
      const c = getClient();
      const ticket = await c.call("recording.upload_ticket", { name: file.name, bytes: file.size });
      withTransition(() => setStep({ name: "uploading", label: file.name, sent: 0, total: file.size }));
      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("PUT", ticket.url);
        xhr.setRequestHeader("Content-Type", "application/octet-stream");
        xhr.upload.onprogress = (ev) => setStep((s) => (s.name === "uploading" ? { ...s, sent: ev.loaded } : s));
        xhr.onload = () => (xhr.status === 200 ? resolve() : reject(new Error(xhr.responseText || "The video didn't come through.")));
        xhr.onerror = () => reject(new Error("The video didn't come through. Is Unbubble still running?"));
        xhr.send(file);
      });
      const c2 = getClient();
      await c2.call("recording.start", { upload_id: ticket.upload_id });
      withTransition(() =>
        setStep({ name: "reading", fraction: 0, message: "Opening the video", fromPhone: false, label: file.name, feed: [], found: 0, eta: null }),
      );
    } catch (e) {
      fail(e);
      withTransition(() => setStep({ name: "choose" }));
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // A video dropped anywhere on the page before (or while) this screen opened.
  useEffect(() => {
    if (!incoming) return;
    onIncomingTaken?.();
    void upload(incoming);
  }, [incoming, upload, onIncomingTaken]);

  if (step.name === "uploading") return <Uploading step={step} />;
  if (step.name === "reading") return <Reading step={step} onCancel={() => void client.call("job.cancel", {})} />;
  if (step.name === "review")
    return <Review step={step} onDone={(info) => go({ name: "confirm", archiveId: step.archiveId, info })} />;
  if (step.name === "confirm") return <Confirm archiveId={step.archiveId} info={step.info} onDone={onDone} onAnother={() => go({ name: "choose" })} />;

  return (
    <main className="stage">
      <section className="lede">
        <button className="btn-link back-link" onClick={onBack}>
          Back
        </button>
        <h1>Record it, scroll it, done.</h1>
        <p>
          Screen-record a conversation on your iPhone while you scroll back through it. Unbubble reads every message, who sent
          it and when, right here on this PC.
        </p>
        <ol className="steps" aria-label="How to record">
          {HOW_TO.map((s) => (
            <li key={s.label} data-state="todo">
              <div>
                {s.label}
                <small>{s.detail}</small>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section className="mirror" aria-live="polite">
        <div className="mirror-body">
          <DropTarget onFile={(f) => void upload(f)} />
          <PhoneVideos onPick={(udid, v) => start({ udid, device_path: v.path }, v.likely_screen_recording ? "Screen recording from your iPhone" : v.name)} />
          <div className="actions">
            <button className="btn btn-quiet" onClick={choose}>
              Choose a video on this PC
            </button>
          </div>
          {error && <Notice explanation={error} onRetry={() => setError(null)} />}
        </div>
        <div className="foot">
          Read on this PC, never uploaded. Light or dark mode, one-to-one or group chats. Photos and emoji are kept as markers.
        </div>
      </section>
    </main>
  );
}

const DEMO = [
  { me: false, w: 52 }, { me: true, w: 38 }, { me: true, w: 61 }, { me: false, w: 44 }, { me: false, w: 70 },
  { me: true, w: 33 }, { me: false, w: 58 }, { me: true, w: 47 }, { me: false, w: 36 }, { me: true, w: 64 },
];

/** The phone, the invitation to drop, and a fallback click-to-choose when dropping isn't possible. */
function DropTarget({ onFile }: { onFile: (f: File) => void }) {
  const status = useConnectStatus();
  const canDrop = status.state === "connected" && status.transport !== "relay";
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div
      className="rec-drop"
      data-over={over}
      data-can={canDrop}
      onDragOver={(e) => {
        if (!canDrop) return;
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        if (!canDrop) return;
        e.preventDefault();
        e.stopPropagation();
        setOver(false);
        const f = e.dataTransfer.files[0];
        if (f) onFile(f);
      }}
    >
      <div className="rec-demo" aria-hidden="true">
        <div className="rec-strip">
          {DEMO.concat(DEMO).map((b, i) => (
            <span key={i} className={b.me ? "me" : undefined} style={{ width: `${b.w}%` }} />
          ))}
        </div>
        <i className="rec-dot" />
      </div>
      <div>
        <h2>{canDrop ? "Drop your recording here" : "Choose your recording"}</h2>
        <p className="sub">
          {canDrop ? (
            <>
              Or{" "}
              <button className="btn-link" onClick={() => input.current?.click()}>
                browse for it
              </button>
              . Nothing leaves this PC.
            </>
          ) : (
            "Nothing leaves this PC. It's read here and saved as a conversation."
          )}
        </p>
        {canDrop && (
          <input
            ref={input}
            type="file"
            accept="video/mp4,video/quicktime,.mp4,.mov,.m4v"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) onFile(f);
              e.target.value = "";
            }}
          />
        )}
      </div>
    </div>
  );
}

/** Videos in the plugged-in iPhone's camera roll, newest first. */
function PhoneVideos({ onPick }: { onPick: (udid: string, v: DeviceVideo) => void }) {
  const client = getClient();
  const listDevices = useCallback(() => client.call("devices.list", {}), [client]);
  const devices = usePoll<Device[]>(listDevices, 3000, true);
  const device = devices?.find((d) => d.state === "ready");
  const [videos, setVideos] = useState<DeviceVideo[] | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    setVideos(null);
    setFailed(null);
    if (!device) return;
    let alive = true;
    client
      .call("recording.device_videos", { udid: device.udid }, 60_000)
      .then((v) => alive && setVideos(v))
      .catch((e) => alive && setFailed(e instanceof Error ? e.message : "Couldn't read the iPhone's videos."));
    return () => {
      alive = false;
    };
  }, [client, device?.udid]); // eslint-disable-line react-hooks/exhaustive-deps

  const shown = useMemo(() => {
    if (!videos) return [];
    const screen = videos.filter((v) => v.likely_screen_recording);
    return (screen.length ? screen : videos).slice(0, 4);
  }, [videos]);

  const any = devices?.[0];
  if (!any) return null;
  if (!device) {
    return <p className="sub">{any.state === "locked" ? "Unlock your iPhone to see its recordings." : "Unlock your iPhone and tap Trust to see its recordings."}</p>;
  }
  if (failed) return <p className="sub">{failed}</p>;
  if (!videos) return <p className="sub rec-searching">Looking for recordings on {device.name ?? "your iPhone"}…</p>;
  if (!shown.length) return <p className="sub">No videos on {device.name ?? "your iPhone"} yet. Record the conversation, then come back.</p>;

  return (
    <div className="rec-videos">
      <p className="sub">On {device.name ?? "your iPhone"}</p>
      <ul>
        {shown.map((v, i) => (
          <li key={v.path} style={{ animationDelay: `${i * 60}ms` }}>
            <button onClick={() => onPick(device.udid, v)}>
              <i aria-hidden="true" />
              <span>
                {v.likely_screen_recording ? "Screen recording" : v.name}
                <small>
                  {ago(v.modified)} · {bytes(v.bytes)}
                </small>
              </span>
              <b aria-hidden="true">Read</b>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Uploading({ step }: { step: Extract<Step, { name: "uploading" }> }) {
  const f = step.total ? step.sent / step.total : 0;
  return (
    <main className="stage">
      <section className="lede">
        <h1>Bringing it over.</h1>
        <p>The video is copied to Unbubble on this PC. It never leaves your computer.</p>
      </section>
      <section className="mirror" data-capturing="" aria-live="polite" aria-busy="true">
        <div className="mirror-body">
          <p className="sub">{step.label}</p>
          <div className="percent" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.floor(f * 100)}>
            {Math.floor(f * 100)}
            <small>%</small>
          </div>
          <div className="track" aria-hidden="true">
            <i style={{ transform: `scaleX(${f})` }} />
          </div>
          <p className="sub">
            {bytes(step.sent)} of {bytes(step.total)}
          </p>
        </div>
      </section>
    </main>
  );
}

function fmtEta(s: number | null): string {
  if (s === null) return "Working out how long this takes";
  if (s < 5) return "Almost done";
  if (s < 60) return `About ${Math.max(5, Math.round(s / 5) * 5)} seconds left`;
  const m = Math.round(s / 60);
  return `About ${m} minute${m === 1 ? "" : "s"} left`;
}

function Reading({ step, onCancel }: { step: Extract<Step, { name: "reading" }>; onCancel: () => void }) {
  const phases = [
    ...(step.fromPhone ? [{ id: "copy", label: "Copy it from your iPhone" }] : []),
    { id: "follow", label: "Follow the scroll" },
    { id: "read", label: "Read every message" },
    { id: "save", label: "Save the conversation" },
  ];
  const m = step.message.toLowerCase();
  const now = m.includes("copying") ? "copy" : m.includes("reading") ? "read" : step.fraction >= 0.98 ? "save" : "follow";
  const current = phases.findIndex((p) => p.id === now);

  return (
    <main className="stage">
      <section className="lede">
        <h1>Reading your recording.</h1>
        <p>Every screen of the conversation is read here, on this PC.</p>
        <ul className="phases" style={{ marginTop: 36 }} aria-label="Progress">
          {phases.map((p, i) => (
            <li key={p.id} data-state={i < current ? "done" : i === current ? "now" : "todo"}>
              {p.label}
            </li>
          ))}
        </ul>
      </section>
      <section className="mirror rec-live" data-capturing="" aria-busy="true">
        <div className="rec-feed" aria-hidden="true">
          {step.feed.length === 0 && <i className="rec-line" />}
          {step.feed.map((b) => (
            <span key={b.id} className={b.from_me ? "me" : undefined}>
              {b.text}
            </span>
          ))}
        </div>
        <div className="mirror-body">
          <p className="sub">{step.label}</p>
          <div className="rec-count" aria-live="polite">
            {step.found}
            <small>{step.found === 1 ? " message" : " messages"}</small>
          </div>
          <div className="track" aria-hidden="true">
            <i style={{ transform: `scaleX(${step.fraction})` }} />
          </div>
          <p className="sub">{now === "read" ? fmtEta(step.eta) : step.message}</p>
        </div>
        <div className="foot">
          <button className="btn btn-quiet" onClick={onCancel}>
            Stop
          </button>
        </div>
      </section>
    </main>
  );
}

function Review({ step, onDone }: { step: Extract<Step, { name: "review" }>; onDone: (info: RecordingInfo) => void }) {
  const client = getClient();
  const [texts, setTexts] = useState<Record<number, string>>(() => Object.fromEntries(step.items.map((i) => [i.index, i.text])));
  const [removed, setRemoved] = useState<Set<number>>(new Set());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Explanation | null>(null);

  async function save() {
    setSaving(true);
    const edits: Record<string, string | null> = {};
    for (const it of step.items) {
      if (removed.has(it.index)) edits[it.index] = null;
      else {
        const t = (texts[it.index] ?? "").trim();
        if (t && t !== it.text) edits[it.index] = t;
      }
    }
    try {
      if (Object.keys(edits).length) await client.call("recording.update", { archive_id: step.archiveId, edits });
      onDone(await client.call("recording.info", { archive_id: step.archiveId }));
    } catch (e) {
      setError(explain(e instanceof HelperError ? e.code : "recording", undefined, e instanceof Error ? e.message : undefined));
      setSaving(false);
    }
  }

  return (
    <main className="stage rec-review-stage">
      <section className="lede">
        <h1>{step.items.length === 1 ? "One to check." : `${step.items.length} to check.`}</h1>
        <p>
          {count(step.info.messages, "message")} read. These {step.items.length === 1 ? "one isn't" : "aren't"} plain text: emoji, a
          sticker, or a bubble that was hard to read. Each is shown as it looked. Type what it says, or leave it as is.
        </p>
        <div className="actions" style={{ marginTop: 28, display: "flex", gap: 10, flexWrap: "wrap" }}>
          <button className="btn btn-primary" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Continue"}
          </button>
          <button className="btn btn-quiet" onClick={() => onDone(step.info)} disabled={saving}>
            Skip
          </button>
        </div>
        {error && <div style={{ marginTop: 16 }}><Notice explanation={error} onRetry={() => setError(null)} /></div>}
      </section>
      <section className="mirror rec-review">
        <ul>
          {step.items.map((it) => {
            const gone = removed.has(it.index);
            return (
              <li key={it.index} data-me={it.from_me} data-gone={gone}>
                {it.before && <p className="rec-context">{it.before}</p>}
                <figure>
                  {it.image ? <img src={`data:image/jpeg;base64,${it.image}`} alt={`How it looked: ${it.text}`} /> : <div className="rec-noimg" />}
                  <figcaption>{it.from_me ? "You" : it.sender}</figcaption>
                </figure>
                <div className="rec-fix">
                  <input
                    className="field"
                    value={texts[it.index] ?? ""}
                    disabled={gone}
                    aria-label="What it says"
                    onChange={(e) => setTexts((t) => ({ ...t, [it.index]: e.target.value }))}
                  />
                  <button
                    className="btn btn-quiet"
                    onClick={() =>
                      setRemoved((r) => {
                        const n = new Set(r);
                        if (n.has(it.index)) n.delete(it.index);
                        else n.add(it.index);
                        return n;
                      })
                    }
                  >
                    {gone ? "Keep" : "Remove"}
                  </button>
                </div>
                {it.after && <p className="rec-context">{it.after}</p>}
              </li>
            );
          })}
        </ul>
      </section>
    </main>
  );
}

function Confirm({ archiveId, info, onDone, onAnother }: { archiveId: string; info: RecordingInfo; onDone: (id: string) => void; onAnother: () => void }) {
  const client = getClient();
  const [title, setTitle] = useState(info.title);
  const [names, setNames] = useState<Record<string, string>>(() =>
    Object.fromEntries(info.people.map((p) => [p.name, info.names[p.name] ?? p.name])),
  );
  const [when, setWhen] = useState(info.recorded_at ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Explanation | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    const renamed = Object.fromEntries(Object.entries(names).filter(([k, v]) => v.trim() && v.trim() !== k));
    const changed = title.trim() !== info.title || Object.keys(renamed).length > 0 || (when && when !== info.recorded_at);
    try {
      if (changed) {
        await client.call("recording.update", {
          archive_id: archiveId,
          ...(title.trim() !== info.title ? { title: title.trim(), names: { ...renamed, ...(info.is_group ? {} : { [info.title]: title.trim() }) } } : { names: renamed }),
          ...(when && when !== info.recorded_at ? { recorded_at: when } : {}),
        });
      }
      onDone(archiveId);
    } catch (e) {
      setError(explain(e instanceof HelperError ? e.code : "recording", undefined, e instanceof Error ? e.message : undefined));
      setSaving(false);
    }
  }

  return (
    <main className="stage">
      <section className="lede">
        <h1>{count(info.messages, "message")} read.</h1>
        <p>
          Check the names and when you recorded it. Messages shows recent times as “Today” or “Tuesday”, so the date is what
          turns those into real dates.
        </p>
        {info.preview.length > 0 && (
          <div className="rec-preview" aria-label="The newest messages">
            {info.preview.map((m, i) => (
              <span key={i} className={m.from_me ? "me" : undefined} style={{ animationDelay: `${i * 70}ms` }}>
                {m.text}
              </span>
            ))}
          </div>
        )}
        {info.warnings.length > 0 && (
          <ul className="rec-notes">
            {info.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        )}
      </section>
      <section className="mirror">
        <form
          className="mirror-body rec-form"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <label>
            <span>{info.is_group ? "Group name" : "Who is this with?"}</span>
            <input className="field" value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={120} />
          </label>
          {info.is_group &&
            info.people.map((p) => (
              <label key={p.name}>
                <span>
                  {p.name === "Unknown" ? "Sender that couldn't be read" : `Shown as “${p.name}”`} · {count(p.messages, "message")}
                </span>
                <input className="field" value={names[p.name] ?? ""} onChange={(e) => setNames((n) => ({ ...n, [p.name]: e.target.value }))} maxLength={120} />
              </label>
            ))}
          <label>
            <span>Recorded</span>
            <input className="field" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
          </label>
          {error && <Notice explanation={error} onRetry={() => setError(null)} />}
          <div className="actions">
            <button className="btn btn-primary" type="submit" disabled={saving}>
              {saving ? "Saving…" : "Open conversation"}
            </button>
            <button className="btn btn-quiet" type="button" onClick={onAnother} disabled={saving}>
              Read another recording
            </button>
          </div>
        </form>
        <div className="foot">From {info.video}. Export it as CSV, PDF, HTML or JSON from the conversation.</div>
      </section>
    </main>
  );
}
