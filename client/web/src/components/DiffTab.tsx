import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'

import { EmptyBlock, Placeholder } from './Blocks'
import { getDiff } from '../services/http'
import { useFactory } from '../services/factory'
import type { TabProps } from './tabs'
import type { Diff } from '../types/api'

/**
 * §8 D3 · F23 — the fifth tab: what this lane changed, and what it committed.
 *
 * "Deciding whether to keep the work is the point of the exercise, and it cannot be done from a
 * transcript." So the files, their `+n/−n`, the commits and one file's patch are all here, on the
 * ticket screen, next to what the agent said it was doing.
 */

/**
 * What the tab row is allowed to say it counts.
 *
 * The label is a function of `TabProps` and is evaluated by the PAGE (`WEB-FEAT-013`'s registry),
 * and a tab cannot re-render its page — so a count this tab fetched is read on the page's next
 * render rather than the moment it lands. It is cached per lane so that switching away and back
 * never asks twice, and `DIFF` with no number is the honest label for a count nobody has read
 * yet: a tab that claimed `· 0 files` before asking would be stating a fact it does not have.
 * `WEB-FIX-036` is the structural fix — a tab publishing its own label.
 */
const counts = new Map<string, number>()
const listeners = new Set<() => void>()
const notify = () => { for (const l of Array.from(listeners)) l() }
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l) } }
let version = 0
const snapshot = () => version

export function diffLabel(props: TabProps): string {
  const count = props.lane ? counts.get(props.lane) : undefined
  if (count === undefined) return 'DIFF'
  return `DIFF · ${count} file${count === 1 ? '' : 's'}`
}

export function DiffTab({ lane }: TabProps) {
  const { reconnects } = useFactory()
  useSyncExternalStore(subscribe, snapshot, snapshot)

  const [state, setState] = useState<{ loading: boolean; diff?: Diff; error?: string }>(
    { loading: true })
  const [open, setOpen] = useState<string | null>(null)
  const [patch, setPatch] = useState<{ path: string; text: string } | null>(null)

  useEffect(() => {
    if (!lane) { setState({ loading: false }); return undefined }
    let alive = true
    setState((current) => ({ ...current, loading: current.diff === undefined }))
    void getDiff(lane).then((answer) => {
      if (!alive) return
      if (answer.ok) {
        counts.set(lane, answer.data.totals.files)
        version += 1
        notify()
        setState({ loading: false, diff: answer.data })
      } else {
        setState({ loading: false, error: answer.error?.message ?? `The server answered ${answer.status}.` })
      }
    })
    return () => { alive = false }
  }, [lane, reconnects])

  // One file's patch, asked for by name — `?path=` exists so that opening a diff does not mean
  // shipping every hunk of every file to draw a list of six names.
  const show = useCallback((path: string) => {
    if (!lane) return
    if (open === path) { setOpen(null); return }
    setOpen(path)
    setPatch(null)
    void getDiff(lane, path).then((answer) => {
      if (answer.ok) setPatch({ path, text: answer.data.patch ?? '' })
    })
  }, [lane, open])

  if (!lane) {
    return (
      <div className="diff-pane">
        <EmptyBlock title="NO LANE" testId="diff-no-lane">
          No lane is claimed for this ticket, so there is no branch to compare.
        </EmptyBlock>
      </div>
    )
  }

  if (state.loading) return <Placeholder what="reading the lane's diff…" />

  if (state.error || !state.diff) {
    return (
      <div className="diff-pane">
        <EmptyBlock title="THE DIFF COULD NOT BE READ" testId="diff-error">
          {state.error ?? 'The lane answered nothing.'}
        </EmptyBlock>
      </div>
    )
  }

  const diff = state.diff
  const nothing = diff.files.length === 0 && diff.commits.length === 0

  return (
    <div className="diff-pane">
      <div className="diff-where" data-testid="diff-where">
        <span className="diff-branch">{diff.branch}</span>
        {diff.base_branch ? <span className="diff-dim">from {diff.base_branch}</span> : null}
      </div>

      {nothing ? (
        <EmptyBlock title="NOTHING CHANGED" testId="diff-empty">
          Nothing changed in this lane yet.
        </EmptyBlock>
      ) : (
        <>
          <div className="diff-totals" data-testid="diff-totals">
            {diff.totals.files} file{diff.totals.files === 1 ? '' : 's'}
            {' · '}+{diff.totals.added} −{diff.totals.removed}
            {' · '}{diff.totals.commits} commit{diff.totals.commits === 1 ? '' : 's'}
          </div>

          <div className="diff-files">
            {diff.files.map((file) => (
              <div key={file.path} className="diff-file" data-testid="diff-file">
                <button type="button" className="diff-open" data-testid="diff-open"
                  aria-expanded={open === file.path} onClick={() => show(file.path)}>
                  <span className="diff-status">{file.status}</span>
                  <span className="diff-path" data-testid="diff-path">{file.path}</span>
                  <span className="diff-added" data-testid="diff-added">+{file.added}</span>
                  <span className="diff-removed" data-testid="diff-removed">−{file.removed}</span>
                  {file.committed ? null : <span className="diff-dim">uncommitted</span>}
                </button>
                {open === file.path && (
                  patch && patch.path === file.path
                    ? <pre className="diff-patch" data-testid="diff-patch">{patch.text}</pre>
                    : <Placeholder what="reading the patch…" />
                )}
              </div>
            ))}
          </div>

          <div className="diff-commits">
            {diff.commits.map((commit) => (
              <div key={commit.sha} className="diff-commit" data-testid="diff-commit">
                <span className="diff-sha">{commit.sha.slice(0, 7)}</span>
                <span className="diff-subject">{commit.subject}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
