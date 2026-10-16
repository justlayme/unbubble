import { useEffect, useRef, useState } from "react";

export function PasswordDialog({ open, onSubmit, onCancel }: { open: boolean; onSubmit: (pw: string) => void; onCancel: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [pw, setPw] = useState("");
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      setPw("");
      d.showModal();
    } else if (!open && d.open) d.close();
  }, [open]);

  return (
    <dialog ref={ref} className="sheet" onCancel={onCancel} aria-labelledby="pw-title">
      <form
        className="sheet-body"
        onSubmit={(e) => {
          e.preventDefault();
          if (pw) onSubmit(pw);
        }}
      >
        <h2 id="pw-title">Enter your backup password</h2>
        <p>
          This iPhone encrypts its computer backups with a password that was set in Finder or iTunes. Unbubble
          needs it to read your messages. It stays on this computer.
        </p>
        <input
          className="field"
          type="password"
          autoComplete="off"
          autoFocus
          placeholder="Backup password"
          aria-label="Backup password"
          value={pw}
          onChange={(e) => setPw(e.target.value)}
        />
        <details>
          <summary className="btn-link" style={{ display: "inline" }}>
            Don't remember it?
          </summary>
          <p style={{ color: "var(--graphite)", fontSize: 14, marginTop: 8 }}>
            On your iPhone, go to Settings → General → Transfer or Reset iPhone → Reset → Reset All Settings. This clears
            the backup password without deleting your messages, photos or apps, but it does reset settings like Wi‑Fi
            passwords and wallpaper.
          </p>
        </details>
        <div className="sheet-actions">
          <button type="button" className="btn btn-quiet" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={!pw}>
            Continue export
          </button>
        </div>
      </form>
    </dialog>
  );
}
