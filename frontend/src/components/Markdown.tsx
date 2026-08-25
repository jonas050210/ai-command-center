// Markdown renderer — GFM tables, task lists, syntax-highlighted code
// blocks with language label + copy button.
import { memo, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import hljs from "highlight.js/lib/common";
import { CheckIcon, CopyIcon } from "../icons";
import { copyText } from "../utils";

function CodeBlock({ language, code }: { language: string; code: string }) {
  const [copied, setCopied] = useState(false);
  const html = useMemo(() => {
    const trimmed = code.replace(/\n$/, "");
    try {
      if (language && hljs.getLanguage(language)) {
        return hljs.highlight(trimmed, { language }).value;
      }
      return hljs.highlightAuto(trimmed).value;
    } catch {
      // escape manually on failure
      return trimmed.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }
  }, [code, language]);

  return (
    <div className="code-block">
      <div className="code-head">
        <span className="code-lang">{language || "text"}</span>
        <button
          className="btn-ghost icon-btn"
          style={{ width: "auto", height: 24, padding: "0 8px", fontSize: 11, gap: 5, display: "inline-flex" }}
          title="Copy code"
          onClick={async () => {
            if (await copyText(code)) {
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1500);
            }
          }}
        >
          {copied ? <CheckIcon className="w-3 h-3 text-good" /> : <CopyIcon className="w-3 h-3" />}
          <span>{copied ? "Copied" : "Copy"}</span>
        </button>
      </div>
      <div className="code-body">
        <pre><code className="hljs" dangerouslySetInnerHTML={{ __html: html }} /></pre>
      </div>
    </div>
  );
}

export const Markdown = memo(function Markdown({ content }: { content: string }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ className, children }) {
            const text = String(children ?? "");
            const match = /language-(\w+)/.exec(className ?? "");
            if (match || text.includes("\n")) {
              return <CodeBlock language={match?.[1] ?? ""} code={text} />;
            }
            return <code className="inline-code">{text}</code>;
          },
          pre({ children }) {
            return <>{children}</>; // CodeBlock renders its own chrome
          },
          table({ children }) {
            return <div className="table-wrap"><table>{children}</table></div>;
          },
          a({ href, children }) {
            return <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>;
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});
