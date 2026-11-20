import type { ReactNode } from 'react'

import { RemedyControl } from './Remedy'
import { tone } from './Chips'
import { TONE } from '../vocabulary'
import type { ErrorBody } from '../types/api'

/**
 * E13's and E18's git refusals, with the facts that make them followable — WEB-FEAT-179.
 *
 * Five refusals in this family are precise, correct, and end with the person leaving the tool:
 * `uncommitted_changes` names how many changes are not committed, `ignored_work` names every path
 * git was told to ignore, `unmerged_commits` names the branch and how many commits only it has,
 * `ticket_not_on_branch` names the branch and the path. Every one of them reached the screen as
 * `error.message` and nothing else — one undifferentiated sentence for three changes and for three
 * hundred, and a refusal about files it would lose that named none of them.
 *
 * Nothing here is composed. Each fact is `detail`'s own value, rendered where a person can read it;
 * the client contributes the label beside it and no arithmetic. A count this component derived
 * from a list it does not have would be a number nobody could check against the server.
 *
 * The control is the SERVER's remedy and it is the non-destructive one, because that is what the
 * wire carries: `error.remedy` is one object (`http.schema.json`), and §11 check 12 gives every one
 * of these codes `open_lane → []` as the remedy the server sends. A destructive way out — the
 * discard `SRV-FIX-059` built — is the surface's own control, and it stays behind a confirmation
 * that names the files (see `ClaimControl`).
 */
export function GitRefusal({ error, testId = 'git-refusal', addressFor, onRetry, children }: {
  error: ErrorBody['error']
  testId?: string
  addressFor?: (lane: string) => string | undefined
  onRetry?: () => void | Promise<void>
  children?: ReactNode
}) {
  const detail = (error.detail ?? {}) as Record<string, unknown>
  const changes = typeof detail.uncommitted_changes === 'number' ? detail.uncommitted_changes : 0
  const commits = typeof detail.commits === 'number' ? detail.commits : 0
  const branch = typeof detail.branch === 'string' ? detail.branch : ''
  const path = typeof detail.path === 'string' ? detail.path : ''
  const ignored = Array.isArray(detail.ignored)
    ? detail.ignored.filter((p): p is string => typeof p === 'string')
    : []

  return (
    <div className="git-refusal" data-testid={testId}>
      <div className="claim-refused" data-testid={`${testId}-message`} style={tone(TONE.crash)}>
        {error.message ?? `the lane refused this (${error.code})`}
      </div>

      {changes > 0 && (
        <div className="claim-refused" data-testid={`${testId}-count`} style={tone(TONE.crash)}>
          {changes} {changes === 1 ? 'change is' : 'changes are'} not committed
        </div>
      )}

      {/* The PATHS, and never a count of them. `ignored_files: 3` is the answer that sent a person
          here; a person deciding whether to lose something is owed what it is. */}
      {ignored.length > 0 && (
        <div className="claim-refused" data-testid={`${testId}-ignored`} style={tone(TONE.crash)}>
          files git was told to ignore, which is where work that cannot be got back lives:
          <ul>{ignored.map((p) => <li key={p}><code>{p}</code></li>)}</ul>
        </div>
      )}

      {branch && (
        <div className="claim-refused" data-testid={`${testId}-branch`} style={tone(TONE.silent)}>
          on <code>{branch}</code>
          {commits > 0 && <> — {commits} {commits === 1 ? 'commit' : 'commits'} no other branch has</>}
        </div>
      )}

      {path && (
        <div className="claim-refused" data-testid={`${testId}-path`} style={tone(TONE.silent)}>
          <code>{path}</code>
        </div>
      )}

      {/* First, always: the way that keeps the work. */}
      <RemedyControl remedy={error.remedy} addressFor={addressFor} onRetry={onRetry} />

      {children}
    </div>
  )
}
