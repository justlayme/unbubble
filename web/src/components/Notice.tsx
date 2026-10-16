import type { Explanation } from "../copy";

export function Notice({
  explanation,
  onRetry,
  onInstallDriver,
}: {
  explanation: Explanation;
  onRetry?: () => void;
  onInstallDriver?: () => void;
}) {
  const a = explanation.action;
  return (
    <div className="notice" data-tone="error" role="alert">
      <strong>{explanation.title}</strong>
      {explanation.body}
      {a && (
        <div className="actions">
          {a.kind === "link" && (
            <a className="btn btn-quiet" href={a.href} target="_blank" rel="noreferrer">
              {a.label}
            </a>
          )}
          {a.kind === "install-driver" && onInstallDriver && (
            <button className="btn btn-primary" onClick={onInstallDriver}>
              {a.label}
            </button>
          )}
          {a.kind === "retry" && onRetry && (
            <button className="btn btn-quiet" onClick={onRetry}>
              {a.label}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
