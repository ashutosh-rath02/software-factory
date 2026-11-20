import { Fragment } from 'react'

/**
 * One chip per declared stage, in order (Ticket.dc.html).
 *
 * "Length and names come from the repo. Done stages are outlined, the active one is tinted, the
 * rest are ghosts." The tool has no opinion about what any stage name means — it is a string
 * from the manifest or from the ticket, and that is the whole of the boundary.
 */
export function StageStrip({ stages, active = -1 }: { stages: readonly string[]; active?: number }) {
  return (
    <div className="stage-strip" data-testid="stage-strip">
      {stages.map((name, i) => (
        <Fragment key={`${name}-${i}`}>
          <span className="stage-chip"
            data-done={String(active >= 0 && i < active)}
            data-active={String(i === active)}>{name}</span>
          {i < stages.length - 1 && <span className="stage-link" />}
        </Fragment>
      ))}
    </div>
  )
}
