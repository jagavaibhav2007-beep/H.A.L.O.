// Phase 1 Step 8 — markdown rendering for chat bubbles (ui_ux/03-chat.md).
// react-markdown with raw HTML left OFF (the default — no rehype-raw): this
// is the error-prevention rule that matters in Phase 2 when model output
// flows through the same component. Code blocks get JetBrains Mono, a copy
// button, and collapse past 20 lines.

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";

const COLLAPSE_LINES = 20;

function CodeBlock({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const lines = text.split("\n");
  const collapsible = lines.length > COLLAPSE_LINES;
  const shown = collapsible && !expanded ? lines.slice(0, COLLAPSE_LINES).join("\n") : text;

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard can be blocked (permissions/insecure context) — a failed
      // copy just leaves the button un-toggled; nothing is lost.
    }
  }

  return (
    <div className="md-code">
      <div className="md-code-bar">
        {collapsible && (
          <button type="button" className="md-code-btn" onClick={() => setExpanded((e) => !e)}>
            {expanded ? "Collapse" : `Show all ${lines.length} lines`}
          </button>
        )}
        <button type="button" className="md-code-btn" onClick={copy}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="md-code-pre">
        <code>{shown}</code>
      </pre>
    </div>
  );
}

const COMPONENTS: Components = {
  // In react-markdown v10 the `inline` prop is gone; a fenced block carries a
  // `language-*` class or spans multiple lines — everything else is inline.
  code({ className, children, ...rest }) {
    const text = String(children ?? "");
    const isBlock = (className?.includes("language-") ?? false) || text.includes("\n");
    if (!isBlock) {
      return (
        <code className="md-inline-code" {...rest}>
          {children}
        </code>
      );
    }
    return <CodeBlock text={text.replace(/\n$/, "")} />;
  },
  // Links open in a new context, never navigating the webview away from the
  // app. ponytail: swap to the Tauri opener plugin for a true system-browser
  // hand-off when it's added; target=_blank is the safe no-nav default until then.
  a({ href, children }) {
    return (
      <a href={href} target="_blank" rel="noreferrer noopener">
        {children}
      </a>
    );
  },
};

export function Markdown({ text }: { text: string }) {
  return (
    <div className="md-body">
      <ReactMarkdown components={COMPONENTS}>{text}</ReactMarkdown>
    </div>
  );
}
