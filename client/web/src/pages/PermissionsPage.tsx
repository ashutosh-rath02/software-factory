import { useCallback, useEffect, useState } from 'react'

import { EmptyBlock, Placeholder, ReconnectBlock } from '../components/Blocks'
import { getPermissionRules, revokeRule, setPolicy } from '../services/http'
import { useEndpoint, useFactory } from '../services/factory'
import {
  POLICIES, POLICY_APPLIES, grantedAt, matchCount, policyLabel, rulesFrom, type Policy,
} from '../services/permissions'
import type { PermissionRule, PermissionRules } from '../types/api'
import type { ServerFrame } from '../types/socket'

/**
 * SPEC.md §4.12 — the standing policy, and what has already been granted.
 *
 * The screen exists because `allow always` is one click: ten of them make a policy nobody can
 * state, and a policy nobody can read is indistinguishable from *never ask*. So there are exactly
 * two things here and §4.12's third part is the word **nothing else** — no per-tool matrix, no
 * wildcard editor, no rule composer. One rule per click, listed, and undoable.
 *
 * **It reads E11 once and is never rebuilt on a clock.** E20, E21 and E23 all broadcast `M12
 * rules` to every client, so the list changes because the tool said it changed — which is also
 * what stops two open tabs disagreeing about a policy that is in force.
 */
export function PermissionsPage() {
  const { onFrame, socketState } = useFactory()
  const live = socketState === 'open'

  const endpoint = useEndpoint<PermissionRules>('permissions', getPermissionRules)
  const answer = endpoint.result?.ok ? endpoint.result.data : undefined

  const [held, setHeld] = useState<PermissionRules | null>(null)
  const [applied, setApplied] = useState<string | null>(null)
  const [refused, setRefused] = useState<string | null>(null)

  // E11's answer, and then M12's every time. The frame carries `policy` and `rules` and no
  // source, so the source is inferred from the one thing that can change it: a policy that is no
  // longer the value E11 reported was set by somebody, in some tab, in this tool process.
  useEffect(() => { if (answer) setHeld(answer) }, [answer])
  useEffect(() => onFrame((frame: ServerFrame) => {
    if (frame.m !== 'rules') return
    setHeld((current) => (current === null ? current : {
      ...current,
      policy: frame.policy,
      policy_source: frame.policy === current.policy ? current.policy_source : 'session',
      rules: rulesFrom(frame.rules),
    }))
  }), [onFrame])

  const choose = useCallback(async (policy: Policy) => {
    setRefused(null)
    const result = await setPolicy(policy)
    if (result.ok) setApplied(policy)
    else setRefused(result.error?.message ?? `the tool refused that policy (HTTP ${result.status})`)
  }, [])

  const revoke = useCallback(async (rule: PermissionRule) => {
    setRefused(null)
    const result = await revokeRule(rule.rule_id)
    // 409 not_revocable names the manifest file. The row that produces it has no button, so
    // reaching this is the tool and the screen disagreeing — and the person is told, not guessed at.
    if (!result.ok) {
      setRefused(result.error?.message
        ?? `${rule.pattern} was not revoked (HTTP ${result.status})`)
    }
  }, [])

  const rules = held?.rules ?? []

  return (
    <div className="perms" data-testid="surface">
      {socketState === 'lost' && <ReconnectBlock />}

      <div className="perms-inner">
        <div>
          <div className="vocab-label">STANDING POLICY</div>
          {held ? (
            <div className="perms-policy" data-testid="policy-control">
              {POLICIES.map((option) => (
                <label className="perms-option" key={option.value} data-testid="policy-option"
                  data-policy={option.value}>
                  <input type="radio" name="permission-policy"
                    data-testid={`policy-input-${option.value}`}
                    value={option.value} checked={held.policy === option.value} disabled={!live}
                    onChange={() => { void choose(option.value) }} />
                  <span className="perms-option-body">
                    <span className="perms-option-label">{option.label}</span>
                    {/* one line of what it MEANS: the value alone is unreadable, and
                        `edit_free` does not say which tools are free */}
                    <span className="perms-option-note" data-testid="policy-consequence">
                      {option.consequence}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          ) : <Placeholder what="reading the policy…" />}

          {/* E21 answers `applies_to: "new_runs"` and means it: a run captures the policy in force
              when it started, while rules are evaluated live. §4.12 wants that said out loud. */}
          <div className="perms-note" data-testid="policy-note">
            {POLICY_APPLIES}
            {held && (
              <span className="perms-source">
                {' '}
                {held.policy_source === 'manifest'
                  ? 'The current value is the manifest’s default.'
                  : 'The current value was set in this session; the manifest is unchanged.'}
              </span>
            )}
          </div>
          {applied && (
            <div className="perms-applied" data-testid="policy-applied">
              {policyLabel(applied)} · applies to lanes started afterwards
            </div>
          )}
        </div>

        <div>
          <div className="vocab-label">GRANTED RULES</div>
          {!held ? <Placeholder what="reading the rules…" /> : rules.length === 0 ? (
            <EmptyBlock title="NOTHING GRANTED" testId="rules-empty">
              Nothing has been granted. Every request will be asked.
            </EmptyBlock>
          ) : (
            <div className="perms-table" data-testid="rules-table" data-dim={String(!live)}>
              <div className="perms-head">
                <span>PATTERN</span>
                <span>SOURCE</span>
                <span>WHEN</span>
                <span>MATCHED</span>
                <span />
              </div>
              {rules.map((rule) => (
                <RuleRow key={rule.rule_id} rule={rule} live={live}
                  onRevoke={() => { void revoke(rule) }} />
              ))}
            </div>
          )}
          {refused && <div className="perms-error" data-testid="permissions-error">{refused}</div>}
        </div>

        {held?.warnings?.map((warning) => (
          // A corrupt rules file is a 200 with an empty rule set and this line — the policy failed
          // CLOSED, to asking, and a screen that showed an empty table without saying why would
          // read as "nothing has ever been granted".
          <div className="perms-warning" data-testid="permissions-warning" key={warning.message}>
            {warning.message}{warning.path ? ` — ${warning.path}` : ''}
          </div>
        ))}
      </div>
    </div>
  )
}

/**
 * One rule. The two kinds differ in exactly one place and it is the last column.
 *
 * A manifest rule is a committed file's, so the row says *declared in the manifest* and links to
 * it instead of offering a revoke that would lie: a UI that silently overrides a committed file is
 * how a repository stops meaning what it says.
 */
function RuleRow({ rule, live, onRevoke }: {
  rule: PermissionRule
  live: boolean
  onRevoke(): void
}) {
  const { manifest } = useFactory()
  return (
    <div className="perms-row" data-testid="rule-row" data-rule={rule.rule_id}
      data-source={rule.source}>
      <span className="perms-pattern">{rule.pattern}</span>
      <span className="perms-source-cell">{rule.source}</span>
      <span className="perms-when">{grantedAt(rule.granted_iso)}</span>
      <span className="perms-matches">{matchCount(rule.match_count)}</span>
      <span className="perms-action">
        {rule.revocable ? (
          <button type="button" className="perms-revoke" data-testid="revoke" disabled={!live}
            onClick={onRevoke}>
            revoke
          </button>
        ) : (
          <span className="perms-declared">
            declared in the{' '}
            <a href={`file://${manifest?.path ?? ''}`} title={manifest?.path}>manifest</a>
          </span>
        )}
      </span>
    </div>
  )
}
