// Wire types shared with the Unbubble helper (unbubble/rpc.py).

export const PROTOCOL_VERSION = 1;
export const PORTS = [47613, 47614, 47615, 47616] as const;

export type Platform = "mac" | "windows" | "linux";

export interface Hello {
  app: string;
  version: string;
  protocol: number;
  platform: Platform;
  os_version: string;
  transport: "direct" | "relay";
  /** Apple's iPhone driver (always ready outside Windows). */
  drivers: DriverStatus;
  export_dir: string;
  job: { kind: "capture" | "export" | "recording"; last: ServerEvent | null } | null;
}

export interface DriverStatus {
  installed: boolean;
  running: boolean;
  winget: boolean;
}

export interface Device {
  udid: string;
  connection: "USB";
  name: string | null;
  model: string | null;
  ios: string | null;
  paired: boolean;
  state: "ready" | "needs_trust" | "locked" | "connected";
  synced: boolean;
  last_sync: number | null;
}

export interface ArchiveInfo {
  id: string;
  source: "iphone" | "mac" | "import" | "screen_recording";
  label: string;
  device_name?: string;
  model?: string;
  ios?: string;
  udid?: string;
  created: number;
  threads?: number;
  messages?: number;
  attachments?: number;
  missing_attachments?: number;
  first_ts?: number | null;
  last_ts?: number | null;
  incremental?: boolean;
  new_files?: number;
  /** Screen recordings: the video it was read from, and when it was recorded (local time). */
  video?: string;
  recorded_at?: string | null;
}

export interface Person {
  id: number;
  name: string;
  addresses: string[];
  is_contact: boolean;
}

export interface Thread {
  id: number;
  title: string;
  is_group: boolean;
  services: string[];
  participants: Person[];
  message_count: number;
  attachment_count: number;
  from_me_count: number;
  first_ts: number | null;
  last_ts: number | null;
  last_preview: string | null;
}

export interface Attachment {
  id: number;
  name: string | null;
  mime: string | null;
  uti: string | null;
  bytes: number | null;
  missing: boolean;
  is_sticker: boolean;
}

export interface Reaction {
  kind: string;
  emoji: string | null;
  part: number;
  from_me: boolean;
  sender: string;
  ts: number | null;
}

export interface Message {
  id: number;
  guid: string;
  ts: number | null;
  from_me: boolean;
  sender: string;
  sender_id: number | null;
  text: string | null;
  subject: string | null;
  service: string | null;
  kind: "message" | "system" | "unsent";
  system_text: string | null;
  effect: string | null;
  app: string | null;
  reply_to: { guid: string; text: string; sender: string } | null;
  edits: { text: string; ts: number | null }[] | null;
  is_unsent: boolean;
  is_audio: boolean;
  read_ts: number | null;
  delivered_ts: number | null;
  attachments: Attachment[];
  reactions: Reaction[];
}

export interface MessagePage {
  messages: Message[];
  next_before: [number, number] | null;
  anchor?: number;
  has_newer?: boolean;
}

export interface SearchHit {
  id: number;
  thread_id: number;
  thread_title: string;
  ts: number | null;
  sender: string;
  snippet: string; // \u0002 … \u0003 mark the matched terms
}

export interface Summary extends ArchiveInfo {
  stats: Record<string, unknown>;
  busiest: { id: number; title: string; message_count: number }[];
  per_year: { year: string; count: number }[];
}

export type ExportFormat = "html" | "pdf" | "json" | "csv";

export type ServerEvent =
  | { event: "capture.phase"; data: { phase: CapturePhase; message: string; incremental?: boolean } }
  | { event: "capture.progress"; data: { phase: CapturePhase; fraction: number; message?: string } }
  | { event: "capture.done"; data: { archive_id: string; summary: Summary } }
  | { event: "capture.failed"; data: HelperErrorData }
  | { event: "capture.cancelled"; data: Record<string, never> }
  | { event: "export.progress"; data: { fraction: number; message: string } }
  | { event: "export.saved"; data: { path: string } }
  | { event: "export.done"; data: { archive_id: string } }
  | { event: "export.failed"; data: HelperErrorData }
  | { event: "export.cancelled"; data: Record<string, never> }
  | { event: "recording.progress"; data: { fraction: number; message: string } }
  | { event: "recording.done"; data: { archive_id: string; summary: Summary } }
  | { event: "recording.failed"; data: HelperErrorData }
  | { event: "recording.cancelled"; data: Record<string, never> }
  | { event: "drivers.progress"; data: { phase: "installing" | "starting"; message: string } };

export type CapturePhase = "trust" | "stream" | "unpack" | "rebuild";

export interface HelperErrorData {
  code: string;
  message: string;
  hint?: string;
  [key: string]: unknown;
}

export class HelperError extends Error {
  readonly code: string;
  readonly data: HelperErrorData;
  constructor(data: HelperErrorData) {
    super(data.message);
    this.code = data.code;
    this.data = data;
  }
}

/** A video in the connected iPhone's camera roll. */
export interface DeviceVideo {
  path: string;
  name: string;
  bytes: number;
  modified: number;
  likely_screen_recording: boolean;
}

/** What was read from a screen recording, for the confirm step. */
export interface RecordingInfo {
  title: string;
  is_group: boolean;
  recorded_at: string | null;
  video: string;
  names: Record<string, string>;
  people: { name: string; messages: number }[];
  messages: number;
  /** Bubbles shown as [Emoji], [Photo or attachment] or read with low confidence. */
  unsure: number;
  warnings: string[];
}

/** Everything the helper can be asked, with parameter and result types. */
export interface Methods {
  hello: [{ client?: string }, Hello];
  "devices.list": [Record<string, never>, Device[]];
  "devices.known": [Record<string, never>, { udid: string; device_name?: string; last_sync?: number; synced: boolean }[]];
  "devices.forget": [{ udid: string }, { forgotten: boolean }];
  "capture.start": [{ udid: string; password?: string }, { started: boolean }];
  "job.cancel": [Record<string, never>, { cancelled: boolean }];
  "drivers.status": [Record<string, never>, DriverStatus];
  "drivers.install": [Record<string, never>, { ok: boolean; reason?: "declined" | "failed" | "not_running"; fallback?: "store"; detail?: string }];
  "archives.list": [Record<string, never>, ArchiveInfo[]];
  "archive.summary": [{ archive_id: string }, Summary];
  "archive.threads": [{ archive_id: string; query?: string }, Thread[]];
  "archive.messages": [{ archive_id: string; thread_id: number; before?: [number, number] | null; limit?: number }, MessagePage];
  "archive.locate": [{ archive_id: string; thread_id: number; message_id: number }, MessagePage];
  "archive.search": [{ archive_id: string; query: string; thread_id?: number }, SearchHit[]];
  "archive.delete": [{ archive_id: string }, { deleted: boolean }];
  "archive.thread_dump": [{ archive_id: string; thread_id: number }, { thread: Thread; messages: Message[] }];
  "attachment.get": [{ archive_id: string; attachment_id: number; size?: "thumb" | "preview" }, { mime: string; name: string; data: string }];
  "export.start": [
    { archive_id: string; formats: ExportFormat[]; thread_ids?: number[]; include_attachments?: boolean; destination?: string },
    { started: boolean; destination: string },
  ];
  "export.reveal": [{ path: string }, { ok: boolean }];
  "export.choose_folder": [{ initial?: string }, { path: string | null; cloud_synced?: boolean }];
  "recording.choose": [Record<string, never>, { path: string | null; name?: string; bytes?: number }];
  "recording.device_videos": [{ udid: string }, DeviceVideo[]];
  "recording.start": [{ path?: string; udid?: string; device_path?: string; recorded_at?: string }, { started: boolean }];
  "recording.info": [{ archive_id: string }, RecordingInfo];
  "recording.update": [
    { archive_id: string; recorded_at?: string; names?: Record<string, string>; title?: string },
    { summary: Summary },
  ];
  "export.check_folder": [{ path: string }, { path: string; cloud_synced: boolean; exists: boolean }];
}
export type Method = keyof Methods;
export type Params<M extends Method> = Methods[M][0];
export type Result<M extends Method> = Methods[M][1];
