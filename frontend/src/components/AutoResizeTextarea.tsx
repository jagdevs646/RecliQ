import { forwardRef, useCallback, useLayoutEffect, useRef, type ForwardedRef, type TextareaHTMLAttributes } from "react";

type Props = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  maxRows?: number;
};

export const AutoResizeTextarea = forwardRef<HTMLTextAreaElement, Props>(function AutoResizeTextarea(
  { className = "", maxRows = 14, onChange, rows = 4, ...props },
  forwardedRef,
) {
  const elementRef = useRef<HTMLTextAreaElement | null>(null);

  const setRef = useCallback((element: HTMLTextAreaElement | null) => {
    elementRef.current = element;
    if (typeof forwardedRef === "function") forwardedRef(element);
    else if (forwardedRef) (forwardedRef as ForwardedRef<HTMLTextAreaElement> & { current: HTMLTextAreaElement | null }).current = element;
  }, [forwardedRef]);

  const resize = useCallback((element: HTMLTextAreaElement | null) => {
    if (!element) return;
    const style = window.getComputedStyle(element);
    const lineHeight = Number.parseFloat(style.lineHeight) || Number.parseFloat(style.fontSize) * 1.5 || 21;
    const verticalExtras = Number.parseFloat(style.paddingTop) + Number.parseFloat(style.paddingBottom)
      + Number.parseFloat(style.borderTopWidth) + Number.parseFloat(style.borderBottomWidth);
    const maxHeight = lineHeight * maxRows + verticalExtras;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, maxHeight)}px`;
    element.style.overflowY = element.scrollHeight > maxHeight ? "auto" : "hidden";
  }, [maxRows]);

  useLayoutEffect(() => resize(elementRef.current));

  return <textarea
    {...props}
    ref={setRef}
    rows={rows}
    className={`resize-none ${className}`.trim()}
    onChange={(event) => {
      resize(event.currentTarget);
      onChange?.(event);
    }}
  />;
});
