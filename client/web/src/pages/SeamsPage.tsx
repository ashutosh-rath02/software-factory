import { useCallback } from 'react'
import { useEndpoint } from '../services/factory'
import { getWiki } from '../services/http'
import { Markdown } from '../components/Markdown'

/**
 * `Seams` — `factory.pen`, frame `EQlEf`, and a DELIBERATE DIVERGENCE from it.
 *
 * The design's frame lists five OPERATIONAL seams — the agent CLI's version, git worktrees,
 * sandbox mounts, the agent's `PATH`, the vendored clone's pin — each with a live state
 * (`drift`, `held`, `behind`) and an action. **Nothing in this tool performs those checks.** No
 * endpoint compares a pinned SHA against a working one or reads a mount plan, so every state on
 * that frame would be a number this surface made up. `docs/operations/ESCAPES.md` has a row for a count and
 * its rows coming from two sources; a confident invented state is the same defect with better
 * typography.
 *
 * What IS real is the repository's own statement about its seams: `MODEL.md` §2 declares six,
 * each an invariant sentence with where it is enforced, and `E27` already serves that document.
 * So this surface renders the repository's §2 rather than a fabricated dashboard, and says
 * plainly what the design's version would need.
 */
export function SeamsPage() {
  const load = useCallback(() => getWiki(), [])
  const { result, loading } = useEndpoint('seams', load, () => true)
  const data = result?.ok ? result.data : undefined
  const model = data?.sections.find(s => s.id === 'MODEL')
  const seams = model ? sectionOf(model.body_markdown, '## 2. Seams') : undefined

  return (
    <div className="page" data-testid="seams-page">
      <section className="block">
        <header className="block-head">
          <h2>Seams</h2>
          {seams && <span className="pill">{count(seams)}</span>}
          <span className="block-note">
            where one repository's assumption meets another's. a seam nobody owns is where the
            next escape comes from.
          </span>
        </header>

        {loading && !data && <p className="block-empty">Reading the model…</p>}
        {data && !model && (
          <p className="block-empty" data-testid="seams-absent">
            This repository declares no <code>MODEL.md</code>, so it states no seams. The seams a
            tool orchestrates are the repository's to name, not this tool's to guess.
          </p>
        )}

        {seams && (
          <>
            <article className="wiki-page">
              <Markdown source={seams} />
            </article>
            <p className="block-empty" data-testid="seams-unchecked">
              These are the seams this repository DECLARES. Whether each one currently holds —
              the agent CLI's version, the worktree root, the sandbox mount plan, the vendored
              clone's pin — is not checked anywhere in this tool, so no state is shown for them.
              A state nobody computed is worse than none.
            </p>
          </>
        )}
      </section>
    </div>
  )
}

/** The markdown between a heading and the next one at the same level. */
function sectionOf(markdown: string, heading: string): string | undefined {
  const start = markdown.indexOf(heading)
  if (start < 0) return undefined
  const after = markdown.slice(start + heading.length)
  const end = after.search(/\n## /)
  return heading + (end < 0 ? after : after.slice(0, end))
}

const count = (section: string) => (section.match(/\n### S\d+/g) ?? []).length
