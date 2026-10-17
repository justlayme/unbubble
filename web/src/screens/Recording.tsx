import { useCallback, useEffect, useMemo, useState } from "react";
import { HelperError, type Device, type DeviceVideo, type RecordingInfo, type ServerEvent } from "../connect/protocol";
import { explain, type Explanation } from "../copy";
import { ago, bytes, count } from "../format";
import { Notice } from "../components/Notice";
import { getClient, useHelperEvents, usePoll, withTransition } from "../useHelper";

/**
 * Turn an iPhone screen recording of a Messages thread into a conversation.
 *
 * iOS doesn't let any app scroll Messages for you, so the person records while they scroll; the
 * video is read on this PC (nothing is uploaded) and saved like any other export, so the library,
 * search, CSV/HTML/PDF/JSON and the upload all work the same.
 */

type Step =
  | { name: "choose" }
  | { name: "reading"; fraction: number; message: string; fromPhone: boolean; label: string }
  | { name: "confirm"; archiveId: string; info: RecordingInfo };

interface Props {
  onBack: () => void;
  onDone: (archiveId: string) => void;
  /** A recording already running when the page loaded (rejoin it). */
  resume?: boolean;
}

const HOW_TO = [
  { label: "Open the conversation on your iPhone", detail: "Start at the newest message." },
  { label: "Start a screen recording", detail: "Control Center, then the record button. Add it in Settings › Control Center if it's missing." },
  { label: "Scroll back slowly to where you want it to start", detail: "Short flicks with a pause between each. Every message needs to be on screen for a moment." },
  { label: "Stop the recording", detail: "It's saved to Photos. Plug in your iPhone to pick it here, or copy the video to this PC." },
];

export function Recording({ onBack, onDone, resume }: Props) {
  const client = getClient();
  const [step, setStep] = useState<Step>(
    resume ? { name: "reading", fraction: 0, message: "Reconnecting to the recording in progress", fromPhone: false, label: "Your recording" } : { name: "choose" },
  );
  const [error, setError] = useState<Explanation | null>(null);
  const go = (s: Step) => withTransition(() => setStep(s));

  useHelperEvents(
    useCallback((e: ServerEvent) => {
      switch (e.event) {
        case "recording.progress":
          setStep((s) => (s.name === "reading" ? { ...s, fraction: Math.max(s.fraction, e.data.fraction), message: e.data.message } : s));
          break;
        case "recording.done":
          void getClient()
            .call("recording.info", { archive_id: e.data.archive_id })
            .then((info) => withTransition(() => setStep({ name: "confirm", archiveId: e.data.archive_id, info })))
            .catch(() => onDone(e.data.archive_id));
          break;
        case "recording.failed":
          setError(explain(e.data.code, e.data.hint, e.data.message));
          withTransition(() => setStep({ name: "choose" }));
          break;
        case "recording.cancelled":
          withTransition(() => setStep({ name: "choose" }));
          break;
      }
    }, [onDone]),
  );

  async function start(params: { path?: string; udid?: string; device_path?: string }, label: string) {
    setError(null);
    try {
      await client.call("recording.start", params);
      go({ name: "reading", fraction: 0, message: "Opening the video", fromPhone: Boolean(params.udid), label });
    } catch (e) {
      setError(explain(e instanceof HelperError ? e.code : "recording", undefined, e instanceof Error ? e.message : undefined));
    }
  }

  async function choose() {
    setError(null);
    try {
      const r = await client.call("recording.choose", {}, 15 * 60_000);
      if (r.path) await start({ path: r.path }, r.name ?? "Your recording");
    } catch (e) {
      setError(explain(e instanceof HelperError ? e.code : "recording", undefined, e instanceof Error ? e.message : undefined));
    }
  }

  if (step.name === "reading") return <Reading step={step} onCancel={() => void client.call("job.cancel", {})} />;
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
          <div className="rec-demo" aria-hidden="true">
            <div className="rec-strip">
              {DEMO.concat(DEMO).map((b, i) => (
                <span key={i} className={b.me ? "me" : undefined} style={{ width: `${b.w}%` }} />
              ))}
            </div>
            <i className="rec-dot" />
          </div>
          <div>
            <h2>Choose your recording</h2>
            <p className="sub" style={{ marginTop: 10 }}>
              Nothing is uploaded. The video is read on this PC and saved as a conversation you can export as CSV, PDF, HTML or JSON.
            </p>
          </div>
          <PhoneVideos onPick={(udid, v) => start({ udid, device_path: v.path }, v.likely_screen_recording ? "Screen recording from your iPhone" : v.name)} />
          <div className="actions">
            <button className="btn btn-quiet" onClick={choose}>
              Choose a video on this PC
            </button>
          </div>
          {error && <Notice explanation={error} onRetry={() => setError(null)} />}
        </div>
        <div className="foot">
          Works with light or dark mode, one-to-one and group chats. Photos and emoji show as placeholders; text is read
          word for word.
        </div>
      </section>
    </main>
  );
}

const DEMO = [
  { me: false, w: 52 }, { me: true, w: 38 }, { me: true, w: 61 }, { me: false, w: 44 }, { me: false, w: 70 },
  { me: true, w: 33 }, { me: false, w: 58 }, { me: true, w: 47 }, { me: false, w: 36 }, { me: true, w: 64 },
];

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
    return (screen.length ? screen : videos).slice(0, 5);
  }, [videos]);

  const any = devices?.[0];
  if (!any) return null;
  if (!device) {
    return <p className="sub">{any.state === "locked" ? "Unlock your iPhone to see its recordings." : "Unlock your iPhone and tap Trust to see its recordings."}</p>;
  }
  if (failed) return <p className="sub">{failed}</p>;
  if (!videos) return <p className="sub">Looking for recordings on {device.name ?? "your iPhone"}…</p>;
  if (!shown.length) return <p className="sub">No videos on {device.name ?? "your iPhone"} yet. Record the conversation, then come back.</p>;

  return (
    <div className="rec-videos">
      <p className="sub">On {device.name ?? "your iPhone"}</p>
      <ul>
        {shown.map((v) => (
          <li key={v.path}>
            <button onClick={() => onPick(device.udid, v)}>
              <span>{v.likely_screen_recording ? "Screen recording" : v.name}</span>
              <small>
                {ago(v.modified)}, {bytes(v.bytes)}
              </small>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Reading({ step, onCancel }: { step: Extract<Step, { name: "reading" }>; onCancel: () => void }) {
  const pct = Math.max(0, Math.min(100, Math.floor(step.fraction * 100)));
  const phases = [
    ...(step.fromPhone ? [{ id: "copy", label: "Copy it from your iPhone" }] : []),
    { id: "follow", label: "Follow the scroll" },
    { id: "read", label: "Read every message" },
    { id: "save", label: "Save the conversation" },
  ];
  const m = step.message.toLowerCase();
  const now = m.includes("copying") ? "copy" : m.includes("reading") ? "read" : step.fraction >= 0.97 ? "save" : "follow";
  const current = phases.findIndex((p) => p.id === now);

  return (
    <main className="stage">
      <section className="lede">
        <h1>Reading your recording.</h1>
        <p>Every screen of the conversation is read on this PC, about a second for each screenful of messages.</p>
        <ul className="phases" style={{ marginTop: 36 }} aria-label="Progress">
          {phases.map((p, i) => (
            <li key={p.id} data-state={i < current ? "done" : i === current ? "now" : "todo"}>
              {p.label}
            </li>
          ))}
        </ul>
      </section>
      <section className="mirror" data-capturing="" aria-live="polite" aria-busy="true">
        <div className="rec-scan" aria-hidden="true">
          <div className="rec-strip">
            {DEMO.concat(DEMO).map((b, i) => (
              <span key={i} className={b.me ? "me" : undefined} style={{ width: `${b.w}%` }} />
            ))}
          </div>
          <i className="rec-line" />
        </div>
        <div className="mirror-body">
          <p className="sub">{step.label}</p>
          <div className="percent" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
            {pct}
            <small>%</small>
          </div>
          <div className="track" aria-hidden="true">
            <i style={{ transform: `scaleX(${step.fraction})` }} />
          </div>
          <p className="sub">{step.message}</p>
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
        {(info.unsure > 0 || info.warnings.length > 0) && (
          <ul className="rec-notes">
            {info.unsure > 0 && (
              <li>
                {count(info.unsure, "bubble")} couldn't be read as text (emoji, photos or stickers) and{" "}
                {info.unsure === 1 ? "is" : "are"} marked in brackets.
              </li>
            )}
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
                <input
                  className="field"
                  value={names[p.name] ?? ""}
                  onChange={(e) => setNames((n) => ({ ...n, [p.name]: e.target.value }))}
                  maxLength={120}
                />
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
