import { useFactory } from '../services/factory'
import { useLanes } from '../services/lanes'

interface Profile { driver: string; models: string[]; default_model: string }

export function useAgentSelection(ticket: string) {
  const { manifest } = useFactory()
  const { selections, selectAgent } = useLanes()
  const profiles: Record<string, Profile> = {}
  for (const [name, raw] of Object.entries(manifest?.agents ?? {})) {
    if (!raw || typeof raw !== 'object') continue
    const p = raw as Partial<Profile>
    if (typeof p.driver === 'string' && typeof p.default_model === 'string'
        && Array.isArray(p.models) && p.models.every((m) => typeof m === 'string')) {
      profiles[name] = p as Profile
    }
  }
  // The normalized legacy profile has no model allowlist and keeps the original start contract.
  const enabled = Object.values(profiles).some((p) => p.models.length > 0)
  const agent = manifest?.default_agent ?? ''
  const selection = selections[ticket] ?? { agent, model: profiles[agent]?.default_model ?? '' }
  const valid = !enabled || !!profiles[selection.agent]?.models.includes(selection.model)
  return { profiles, enabled, valid, selection: enabled ? selection : undefined,
    choose: (agent: string, model = profiles[agent]?.default_model ?? '') =>
      selectAgent(ticket, { agent, model }) }
}

export function AgentSelector({ ticketId, disabled = false }: {
  ticketId: string; disabled?: boolean
}) {
  const { profiles, selection, choose, enabled, valid } = useAgentSelection(ticketId)
  if (!enabled || !selection) return null
  return (
    <fieldset className="agent-selector" disabled={disabled}>
      <legend>Run with</legend>
      <label>Agent
        <select aria-label="Agent" value={selection.agent}
          onChange={(event) => choose(event.target.value)}>
          {Object.entries(profiles).map(([name, profile]) => (
            <option key={name} value={name}>{name} ({profile.driver === 'codex' ? 'Codex / GPT' : 'Claude'})</option>
          ))}
        </select>
      </label>
      <label>Model
        <select aria-label="Model" value={selection.model}
          onChange={(event) => choose(selection.agent, event.target.value)}>
          {(profiles[selection.agent]?.models ?? []).map((model) => (
            <option key={model} value={model}>{model}</option>
          ))}
        </select>
      </label>
      {!valid && <span role="alert">Select an available agent and model before starting.</span>}
    </fieldset>
  )
}
