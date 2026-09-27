import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Render markdown client-side. XSS posture mirrors the legacy server-side
 * `_markdown()` in webapp.py: raw HTML in user content is NEVER executed.
 *
 * - `remarkPlugins={[remarkGfm]}` enables GitHub-flavoured tables, task lists,
 *   autolinks, strikethrough.
 * - We omit `rehype-raw` on purpose: passing raw HTML through would defeat
 *   the security goal. If a doc needs richer formatting, use markdown.
 *
 * The body string is the doc's raw text; we strip a leading frontmatter block
 * (the server already parsed it into doc.frontmatter and the UI surfaces it
 * in the header, so we don't render it twice).
 */
export default function MarkdownView({ body }: { body: string }) {
  const stripped = stripFrontmatter(body);
  return (
    <article className="prose">
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
        {stripped}
      </ReactMarkdown>
    </article>
  );
}

function stripFrontmatter(text: string): string {
  // The legacy split_frontmatter() is identical: a leading `---\n…\n---\n`
  // block, blank line, then body. Anything that doesn't look like frontmatter
  // is left as-is — this never raises.
  const stripped = text.replace(/^﻿/, "");
  if (!stripped.startsWith("---")) return text;
  const lines = stripped.split("\n");
  if (lines[0].trim() !== "---") return text;
  const end = lines.slice(1).findIndex((l) => l.trim() === "---");
  if (end === -1) return text;
  return lines.slice(end + 2).join("\n").replace(/^\n+/, "");
}