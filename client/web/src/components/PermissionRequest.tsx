import { useCallback, useEffect, useState } from 'react'
import { WarningCircle } from '@phosphor-icons/react'

import { decidePermission } from '../services/http'
import { useFactory } from '../services/factory'
import type { Decision, PermissionAsk } from '../services/permissions'
import type { ServerFrame } from '../types/socket'

/**
 * SPEC.md §4.6's `permission_request` entry, and §4.12's inline request — the design's `held`
 * rectangle (`Ticket.dc.html`) in the accent colour, with three buttons.
 *
 * **The order inside it is the feature.** `rule_that_would_be_written` and, when there is one,
 * the rule that nearly matched, are rendered ABOVE the buttons: a rule shown underneath the
 * button that writes it is a rule shown after the decision. `always` writes a permanent rule into
 * a file a human commits, and §1 of the ticket is why this screen exists at all — one click, ten
 * clicks, and a policy nobody can state.
 *
 * **Nothing here grants anything by itself.** No key binding, no `autoFocus`, no default focus, no
 * timer: the only path to `allow always` is a click on the control that says `allow always`, and
 * `permissions.test.tsx::nothing_grants_itself` asserts each of those four separately AND greps
 * this file for them, because the mistake is one line long and reads as a convenience.
 *
 * `M7 permission.resolved` is a BROADCAST, so a request answered in another tab stops waiting
 * here without anything being refetched. It stops waiting when the run dies too (`voided`), which
 * is the whole reason E23 answers `410 gone` rather than writing the rule.
 */

const RESOLVED: Record<string, string> = {
  once: 'allowed once',
  always: 'rule added',
  deny: 'denied',
  voided: 'voided · the run ended before anybody answered',
}

interface Resolution { decision: string; here: boolean }

/**
 * §4.6's `permission_decision` vocabulary — `allowed once` / `rule added · <rule>` / `denied` —
 * and, when the answer came from somewhere else, the fact that it did. A request left showing three
 * live buttons after it has been answered is a request a person presses twice and gets a 404 for.
 */
function resolvedLine(resolution: Resolution, rule: string): string {
  if (resolution.decision === 'answered') return 'answered elsewhere · already resolved'
  const phrase = resolution.decision === 'always'
    ? `${RESOLVED.always} · ${rule}`
    : RESOLVED[resolution.decision] ?? resolution.decision
  return resolution.here ? phrase : `answered elsewhere · ${phrase}`
}

export function PermissionRequestBlock({ ask }: { ask: PermissionAsk }) {
  const { onFrame, socketState } = useFactory()
  const [resolution, setResolution] = useState<Resolution | null>(null)
  const [refused, setRefused] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const live = socketState === 'open'

  useEffect(() => onFrame((frame: ServerFrame) => {
    if (frame.m !== 'permission.resolved' || frame.request_id !== ask.request_id) return
    setResolution((held) => held ?? { decision: frame.decision, here: false })
  }), [onFrame, ask.request_id])

  const answer = useCallback(async (decision: Decision) => {
    setSending(true)
    const result = await decidePermission(ask.request_id, decision)
    setSending(false)
    if (result.ok) {
      setResolution({ decision, here: true })
      return
    }
    // 404 with `detail.state: "answered"` is the ordinary two-tab race and 410 is a run that has
    // since died — neither is an error to log, both are answers, and in both the request is over.
    const state = (result.error?.detail as { state?: string } | undefined)?.state
    if (result.status === 404 && state === 'answered') {
      setResolution({ decision: 'answered', here: false })
    } else if (result.status === 410) {
      setResolution({ decision: 'voided', here: false })
    } else {
      setRefused(result.error?.message ?? `the tool refused this answer (HTTP ${result.status})`)
    }
  }, [ask.request_id])

  const pending = resolution === null

  return (
    <div className="held-block perm-request" data-testid="permission-request" data-tone="accent"
      data-pending={String(pending)}>
      <WarningCircle size={15} color="var(--color-accent)" />
      <div className="perm-body">
        <div className="held-title">PERMISSION REQUESTED · {ask.tool}</div>
        <div className="held-body" data-testid="request-input">{ask.input_summary}</div>

        {/* before the buttons, always — this is what `allow always` would write, and nothing
            broader: the tool stored the string it showed and writes that one back. */}
        <div className="perm-rule" data-testid="request-rule">
          <span className="perm-rule-label">allow always would write</span>
          <span className="perm-rule-pattern">{ask.rule_that_would_be_written}</span>
        </div>

        {ask.nearest_miss && (
          <div className="perm-miss" data-testid="request-miss">
            <span className="perm-rule-pattern">{ask.nearest_miss.pattern}</span>
            {' nearly matched — '}
            {ask.nearest_miss.why_it_did_not_match}
          </div>
        )}

        {pending ? (
          <div className="perm-buttons">
            <button type="button" className="perm-button" data-testid="allow-once"
              disabled={sending || !live} onClick={() => { void answer('once') }}>
              allow once
            </button>
            <button type="button" className="perm-button perm-button-always"
              data-testid="allow-always" disabled={sending || !live}
              onClick={() => { void answer('always') }}>
              allow always
            </button>
            <button type="button" className="perm-button" data-testid="deny"
              disabled={sending || !live} onClick={() => { void answer('deny') }}>
              deny
            </button>
          </div>
        ) : (
          <div className="perm-resolved" data-testid="request-resolved">
            {resolvedLine(resolution, ask.rule_that_would_be_written)}
          </div>
        )}

        {refused && <div className="perm-error" data-testid="request-error">{refused}</div>}
      </div>
      {pending && (
        <div className="held-aside">
          {live ? 'the run is stopped until you answer' : 'reconnecting · cannot answer'}
        </div>
      )}
    </div>
  )
}
