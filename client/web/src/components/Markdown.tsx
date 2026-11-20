import MarkdownIt from 'markdown-it'
import { useMemo } from 'react'

/**
 * The document renderer, and the one deliberate decision in it: `html: false`.
 *
 * The documents come from the repository being worked on, and in this product an AGENT writes
 * the reports. `html: false` is a refusal at the PARSER, not a sanitiser bolted on after:
 * embedded HTML is escaped and shown as text — never executed, and never silently dropped.
 *
 *   not executed — a delivery report that can run script inside the tool supervising the agent
 *                  that wrote it is the whole threat model of a local tool with no auth, and
 *                  SPEC.md §1.1 makes loopback-only the reason there is no permission system.
 *   not dropped  — the tool's premise is that the documents are the state. A renderer that
 *                  strips <div> shows a document the file does not contain. Escaping is the
 *                  honest failure: the reader sees exactly what is on disk.
 *
 * `linkify` stays off for the same reason: a bare URL in a ticket is text, and auto-linking
 * invents a control the document did not ask for.
 *
 * Rejected: `marked` (raw HTML passes through by default — safe only with DOMPurify configured
 * correctly, and a sanitiser one option away from wrong is worse than a parser that never parses
 * HTML) and `react-markdown` (the whole unified/remark tree for a read-only pane).
 */
const md = new MarkdownIt({
  html: false,
  linkify: false,
  typographer: false,
  breaks: false,
})

export function Markdown({ source }: { source: string }) {
  const html = useMemo(() => md.render(source ?? ''), [source])
  // eslint-disable-next-line react/no-danger -- the parser above emits no HTML from the source
  return <div className="md" data-testid="doc-body" dangerouslySetInnerHTML={{ __html: html }} />
}
