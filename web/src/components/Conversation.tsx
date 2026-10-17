import { Fragment, useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import type { Attachment, Message, MessagePage, Thread } from "../connect/protocol";
import { bytes, count, fmtDay, fmtTime, span } from "../format";
import { getClient } from "../useHelper";

interface Props {
  archiveId: string;
  thread: Thread;
  anchorId: number | null;
  onBack: () => void;
  onJumpToLatest: () => void;
  actions?: ReactNode;
}

const URL_RE = /\bhttps?:\/\/[^\s<]+[^\s<.,;:!?)\]'"]/g;

function RichText({ text }: { text: string }) {
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(URL_RE)) {
    out.push(text.slice(last, m.index));
    out.push(
      <a key={m.index} href={m[0]} target="_blank" rel="noopener noreferrer">
        {m[0]}
      </a>,
    );
    last = (m.index ?? 0) + m[0].length;
  }
  out.push(text.slice(last));
  return <>{out}</>;
}

function isImage(a: Attachment) {
  return (a.mime ?? "").startsWith("image/") || /\.(heic|heif|jpe?g|png|gif|webp)$/i.test(a.name ?? "");
}

function Thumb({ archiveId, a, onOpen }: { archiveId: string; a: Attachment; onOpen: (a: Attachment) => void }) {
  const ref = useRef<HTMLButtonElement>(null);
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          io.disconnect();
          getClient().attachmentUrl(archiveId, a.id, "thumb").then(setSrc, () => setFailed(true));
        }
      },
      { rootMargin: "600px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [archiveId, a.id]);
  if (failed) return <FileChip a={a} />;
  return (
    <button ref={ref} onClick={() => onOpen(a)} aria-label={`Open ${a.name ?? "photo"}`}>
      {src ? <img className="thumb" src={src} alt={a.name ?? "Photo"} /> : <span className="thumb" />}
    </button>
  );
}

function FileChip({ a }: { a: Attachment }) {
  if (a.missing)
    return (
      <span className="file-chip" data-missing="true" title="The iPhone only kept this in iCloud">
        {a.name ?? "Attachment"} isn't stored on the device
      </span>
    );
  const kind = (a.mime ?? "").startsWith("video/") ? "Video" : (a.mime ?? "").startsWith("audio/") ? "Audio" : "File";
  return (
    <span className="file-chip">
      {kind}: {a.name ?? "attachment"} {a.bytes ? `(${bytes(a.bytes)})` : ""}, included when you save
    </span>
  );
}

export function Conversation({ archiveId, thread, anchorId, onBack, onJumpToLatest, actions }: Props) {
  const client = getClient();
  const stream = useRef<HTMLDivElement>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [cursor, setCursor] = useState<[number, number] | null>(null);
  const [hasNewer, setHasNewer] = useState(false);
  const [loading, setLoading] = useState(false);
  const [lightbox, setLightbox] = useState<{ src: string; name: string } | null>(null);
  const [openEdits, setOpenEdits] = useState<Set<number>>(new Set());
  const restore = useRef<{ height: number; top: number } | null>(null);
  const initialScroll = useRef(true);
  const lightboxRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    let alive = true;
    const req: Promise<MessagePage> = anchorId
      ? client.call("archive.locate", { archive_id: archiveId, thread_id: thread.id, message_id: anchorId })
      : client.call("archive.messages", { archive_id: archiveId, thread_id: thread.id, limit: 200 });
    req.then((page) => {
      if (!alive) return;
      setMessages(page.messages);
      setCursor(page.next_before);
      setHasNewer(Boolean(page.has_newer));
    }, () => {});
    return () => {
      alive = false;
    };
  }, [client, archiveId, thread.id, anchorId]);

  const loadOlder = useCallback(async () => {
    if (!cursor || loading || !stream.current) return;
    setLoading(true);
    restore.current = { height: stream.current.scrollHeight, top: stream.current.scrollTop };
    try {
      const page = await client.call("archive.messages", { archive_id: archiveId, thread_id: thread.id, before: cursor, limit: 200 });
      setMessages((m) => [...page.messages, ...m]);
      setCursor(page.next_before);
    } finally {
      setLoading(false);
    }
  }, [client, archiveId, thread.id, cursor, loading]);

  // Keep the reader's place when older messages are prepended; open at the latest (or the hit).
  useLayoutEffect(() => {
    const el = stream.current;
    if (!el || messages.length === 0) return;
    if (restore.current) {
      el.scrollTop = el.scrollHeight - restore.current.height + restore.current.top;
      restore.current = null;
    } else if (initialScroll.current) {
      initialScroll.current = false;
      if (anchorId) el.querySelector(`[data-id="${anchorId}"]`)?.scrollIntoView({ block: "center" });
      else el.scrollTop = el.scrollHeight;
    }
  }, [messages, anchorId]);

  async function openImage(a: Attachment) {
    try {
      const src = await client.attachmentUrl(archiveId, a.id, "preview");
      setLightbox({ src, name: a.name ?? "Photo" });
      requestAnimationFrame(() => lightboxRef.current?.showModal());
    } catch {
      /* missing: the chip already says so */
    }
  }

  let lastDay = "";
  return (
    <section className="convo" aria-label={`Conversation with ${thread.title}`}>
      <div className="convo-head">
        <div>
          <button className="btn-link only-narrow" onClick={onBack}>
            All conversations
          </button>
          <h2>{thread.title}</h2>
          <p>
            {count(thread.message_count, "message")}
            {thread.first_ts ? `, ${span(thread.first_ts, thread.last_ts)}` : ""}
            {thread.participants.length > 0 &&
              thread.participants.some((p) => p.addresses.length > 0) &&
              `, ${thread.participants.filter((p) => p.addresses.length > 0).map((p) => p.addresses.join(", ")).join("; ")}`.slice(0, 160)}
          </p>
        </div>
        {actions}
      </div>
      <div
        className="stream"
        ref={stream}
        tabIndex={0}
        onScroll={(e) => {
          if (e.currentTarget.scrollTop < 800) void loadOlder();
        }}
      >
        {cursor && <div className="loading-more">{loading ? "Loading earlier messages…" : " "}</div>}
        {messages.map((m, i) => {
          const day = fmtDay(m.ts);
          const showDay = day !== lastDay;
          lastDay = day;
          const prev = messages[i - 1];
          if (m.kind === "system") {
            return (
              <Fragment key={m.id}>
                {showDay && <div className="day">{day}</div>}
                <div className="sys">
                  {m.system_text} {fmtTime(m.ts)}
                </div>
              </Fragment>
            );
          }
          const showWho = thread.is_group && !m.from_me && (showDay || !prev || prev.sender_id !== m.sender_id || prev.from_me || prev.kind === "system");
          const images = m.attachments.filter((a) => isImage(a) && !a.missing);
          const others = m.attachments.filter((a) => !(isImage(a) && !a.missing));
          // One time under each run of bubbles sent the same minute by the same side, as Messages does.
          const next = messages[i + 1];
          const time = fmtTime(m.ts);
          const lastOfMinute = !next || next.kind === "system" || next.from_me !== m.from_me || fmtTime(next.ts) !== time || fmtDay(next.ts) !== day;
          const meta = [
            lastOfMinute ? time : null,
            m.service && m.service !== "iMessage" ? m.service : null,
            m.effect ? `Sent with ${m.effect}` : null,
            m.app && m.app !== "link" ? m.app : null,
            m.from_me && m.read_ts ? `Read ${fmtTime(m.read_ts)}` : null,
          ].filter(Boolean);
          return (
            <Fragment key={m.id}>
              {showDay && <div className="day">{day}</div>}
              <div className={`msg${m.from_me ? " me" : ""}${m.id === anchorId ? " flash" : ""}`} data-id={m.id}>
                {showWho && <div className="who">{m.sender}</div>}
                {m.reply_to && (
                  <div className="quote">
                    {m.reply_to.sender}: {m.reply_to.text}
                  </div>
                )}
                {(images.length > 0 || others.length > 0) && (
                  <div className="media">
                    {images.map((a) => (
                      <Thumb key={a.id} archiveId={archiveId} a={a} onOpen={openImage} />
                    ))}
                    {others.map((a) => (
                      <FileChip key={a.id} a={a} />
                    ))}
                  </div>
                )}
                {m.kind === "unsent" ? (
                  <div className="bubble unsent">{m.from_me ? "You" : m.sender} unsent a message</div>
                ) : (
                  (m.text || m.subject) && (
                    <div className="bubble">
                      {m.subject && <strong style={{ display: "block" }}>{m.subject}</strong>}
                      {m.text && <RichText text={m.text} />}
                    </div>
                  )
                )}
                {m.reactions.length > 0 && (
                  <div className="reacts">
                    {m.reactions.map((r, j) => (
                      <span key={j} className="react" title={`${r.sender} reacted ${r.emoji ?? r.kind}`}>
                        {r.emoji ?? "•"}
                      </span>
                    ))}
                  </div>
                )}
                <div className="meta-line">
                  {meta.join(", ")}
                  {m.edits && (
                    <>
                      {", "}
                      <button
                        className="btn-link"
                        style={{ fontSize: "inherit" }}
                        aria-expanded={openEdits.has(m.id)}
                        onClick={() =>
                          setOpenEdits((s) => {
                            const n = new Set(s);
                            if (n.has(m.id)) n.delete(m.id);
                            else n.add(m.id);
                            return n;
                          })
                        }
                      >
                        Edited
                      </button>
                    </>
                  )}
                </div>
                {m.edits && openEdits.has(m.id) && (
                  <div className="edits">
                    Edit history
                    <ol>
                      {m.edits.map((e, k) => (
                        <li key={k}>
                          {e.text} {e.ts ? `(${fmtTime(e.ts)})` : ""}
                        </li>
                      ))}
                    </ol>
                  </div>
                )}
              </div>
            </Fragment>
          );
        })}
        {hasNewer && (
          <div style={{ textAlign: "center", marginTop: 20 }}>
            <button className="btn btn-quiet" onClick={onJumpToLatest}>
              Jump to the latest messages
            </button>
          </div>
        )}
      </div>
      <dialog ref={lightboxRef} className="lightbox" onClick={() => lightboxRef.current?.close()} onClose={() => setLightbox(null)}>
        {lightbox && (
          <>
            <img src={lightbox.src} alt={lightbox.name} />
            <p>{lightbox.name}</p>
          </>
        )}
      </dialog>
    </section>
  );
}
