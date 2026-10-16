import { useEffect, useMemo, useState } from "react";
import type { SearchHit, Summary, Thread } from "../connect/protocol";
import { HelperError } from "../connect/protocol";
import { count, span, threadDate } from "../format";
import { getClient } from "../useHelper";
import { Conversation } from "../components/Conversation";
import { ExportDialog } from "../components/ExportDialog";

export interface UploadPayload {
  thread: Thread;
  messages: unknown[];
  source: { device: string; exported_at: number };
}

interface Props {
  archiveId: string;
  onBack: () => void;
  onUpload?: (payload: UploadPayload) => void | Promise<void>;
  uploadLabel?: string;
}

function Snippet({ text }: { text: string }) {
  // The helper marks matched terms with \u0002 … \u0003.
  const parts = text.split(/(\u0002[^\u0003]*\u0003)/g);
  return (
    <>
      {parts.map((p, i) => (p.startsWith("\u0002") ? <mark key={i}>{p.slice(1, -1)}</mark> : p))}
    </>
  );
}

export function Library({ archiveId, onBack, onUpload, uploadLabel = "Upload" }: Props) {
  const client = getClient();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [selected, setSelected] = useState<Thread | null>(null);
  const [anchor, setAnchor] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [exporting, setExporting] = useState(false);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    client.call("archive.summary", { archive_id: archiveId }).then(setSummary, () => {});
    client.call("archive.threads", { archive_id: archiveId }).then((t) => {
      setThreads(t);
      if (window.matchMedia("(min-width: 761px)").matches) setSelected((s) => s ?? t[0] ?? null);
    }, () => {});
  }, [client, archiveId]);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setHits([]);
      return;
    }
    const id = setTimeout(() => {
      client.call("archive.search", { archive_id: archiveId, query: q }).then(setHits, () => setHits([]));
    }, 180);
    return () => clearTimeout(id);
  }, [client, archiveId, query]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return threads;
    return threads.filter(
      (t) => t.title.toLowerCase().includes(q) || t.participants.some((p) => p.addresses.some((a) => a.toLowerCase().includes(q))),
    );
  }, [threads, query]);

  async function upload() {
    if (!selected || !onUpload) return;
    setSending(true);
    try {
      const dump = await client.call("archive.thread_dump", { archive_id: archiveId, thread_id: selected.id }, 600_000);
      await onUpload({
        thread: dump.thread,
        messages: dump.messages,
        source: { device: summary?.device_name ?? summary?.label ?? "iPhone", exported_at: summary?.created ?? Date.now() },
      });
    } catch (e) {
      if (e instanceof HelperError) alert(e.message);
    } finally {
      setSending(false);
    }
  }

  const stats = summary?.stats as { messages?: number; threads?: number; first_ts?: number; last_ts?: number } | undefined;

  return (
    <div className="library" data-open={selected ? "true" : "false"}>
      <header className="lib-head">
        <div>
          <button className="btn-link" onClick={onBack}>
            All exports
          </button>
          <h1 style={{ marginTop: 8 }}>{summary?.device_name ?? summary?.label ?? "Messages"}</h1>
          {stats && (
            <p className="meta">
              {count(stats.messages, "message")} in {count(stats.threads, "conversation")}
              {stats.first_ts ? `, ${span(stats.first_ts, stats.last_ts)}` : ""}
            </p>
          )}
        </div>
        <div className="tools">
          <input
            className="field"
            type="search"
            placeholder="Search names and every message"
            aria-label="Search names and every message"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button className="btn btn-primary" onClick={() => setExporting(true)}>
            Save to this computer
          </button>
        </div>
      </header>

      <div className="lib-body">
        <nav className="threads" aria-label="Conversations">
          {query.trim() && filtered.length > 0 && <div className="group-label">{count(filtered.length, "conversation")}</div>}
          <ul>
            {filtered.map((t) => (
              <li key={t.id}>
                <button
                  aria-current={selected?.id === t.id && anchor === null}
                  onClick={() => {
                    setAnchor(null);
                    setSelected(t);
                  }}
                >
                  <span className="t-title">{t.title}</span>
                  <span className="t-date">{threadDate(t.last_ts)}</span>
                  <span className="t-sub">{t.last_preview ?? count(t.message_count, "message")}</span>
                </button>
              </li>
            ))}
          </ul>
          {hits.length > 0 && (
            <>
              <div className="group-label">{count(hits.length, "message")}{hits.length === 100 ? "+" : ""}</div>
              <ul>
                {hits.map((h) => (
                  <li key={h.id}>
                    <button
                      aria-current={anchor === h.id}
                      onClick={() => {
                        setSelected(threads.find((t) => t.id === h.thread_id) ?? null);
                        setAnchor(h.id);
                      }}
                    >
                      <span className="t-title">{h.thread_title}</span>
                      <span className="t-date">{threadDate(h.ts)}</span>
                      <span className="t-sub hit">
                        {h.sender}: <Snippet text={h.snippet} />
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
          {query.trim() && filtered.length === 0 && hits.length === 0 && (
            <p className="empty" style={{ padding: "40px 12px" }}>
              Nothing matches “{query.trim()}”.
            </p>
          )}
        </nav>

        {selected ? (
          <Conversation
            key={`${selected.id}:${anchor ?? "latest"}`}
            archiveId={archiveId}
            thread={selected}
            anchorId={anchor}
            onBack={() => setSelected(null)}
            onJumpToLatest={() => setAnchor(null)}
            actions={
              onUpload ? (
                <button className="btn btn-quiet" disabled={sending} onClick={upload}>
                  {sending ? "Uploading…" : uploadLabel}
                </button>
              ) : null
            }
          />
        ) : (
          <div className="convo">
            <div className="empty">
              <strong>Choose a conversation</strong>
              Everything stays on this computer until you choose to save or share it.
            </div>
          </div>
        )}
      </div>

      {summary && (
        <ExportDialog
          open={exporting}
          archiveId={archiveId}
          current={selected}
          totalThreads={threads.length}
          onClose={() => setExporting(false)}
        />
      )}
    </div>
  );
}
