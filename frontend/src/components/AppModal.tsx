import { useEffect, useId, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

type AppModalProps = {
  open: boolean;
  title: string;
  eyebrow?: string;
  size?: "standard" | "wide";
  onClose: () => void;
  children: ReactNode;
};

export function AppModal({ open, title, eyebrow, size = "standard", onClose, children }: AppModalProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    else if (!open && dialog.open) dialog.close();
  }, [open]);

  return <dialog
    ref={dialogRef}
    className={`app-modal app-modal--${size}`}
    aria-labelledby={titleId}
    aria-modal="true"
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
  >
    <header className="app-modal-header">
      <div className="app-modal-heading">
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        <h2 id={titleId}>{title}</h2>
      </div>
      <button type="button" className="icon-button" aria-label={`Close ${title}`} title="Close" onClick={onClose}>
        <X size={17} aria-hidden="true" />
      </button>
    </header>
    <div className="app-modal-body">{children}</div>
  </dialog>;
}
