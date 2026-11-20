export function standaloneIsLast(root) {
  const rows = [...root.querySelectorAll('[data-testid="requirement-row"]')]
  return rows.length > 1
    && rows.filter((row) => row.getAttribute('data-standalone') === 'true').length === 1
    && rows.at(-1)?.getAttribute('data-standalone') === 'true'
}

export function orchardCheck(manifest, backlog, names, counts, pill) {
  const declared = Object.keys(manifest?.repos ?? {})
  return {
    name: 'the orchard draws the repositories E2 declares, each counting E29’s backlog',
    passed: declared.length > 0 && names.join(' | ') === declared.join(' | ')
      && pill === String(declared.length)
      && counts.length === declared.length
      && counts.every((count) => count === `${backlog?.total} tickets`),
    detail: `pill "${pill}" · on screen ${names.join('/') || 'none'} vs E2 `
      + `${declared.join('/') || 'none'} · counts ${counts.join('/') || 'none'} `
      + `vs E29 total ${backlog?.total}`,
  }
}

export function deliveryCheck(home, ledger, drawn, metric) {
  const served = [
    ...(home?.needs_you?.rows ?? []).map((row) => row.ticket_id),
    ...(home?.unpushed?.rows ?? []).map((row) => row.ticket_id),
  ]
  return {
    name: "delivery reads E28's ledger and E30's blocked rows, each from its own source",
    passed: served.length > 0 && drawn.join(' | ') === served.join(' | ')
      && metric === String(ledger?.total) && (ledger?.total ?? 0) > 0,
    detail: `blocked on screen ${drawn.join('/') || 'none'} vs E30 ${served.join('/') || 'none'} `
      + `· escapes metric "${metric}" vs E28 total ${ledger?.total}`,
  }
}

export function producingControlCheck(lane, controls) {
  return {
    name: 'and it is still exactly ONE control once the lane is producing, reading Stop',
    passed: lane?.state?.process === 'running' && lane?.state?.motion?.value === 'producing'
      && controls.length === 1 && controls[0].mode === 'stop' && /^Stop$/.test(controls[0].label),
    detail: `${controls.map((control) => `${control.label} (${control.mode})`).join(' · ') || 'none'}`
      + ` — ${lane?.lane ?? 'missing lane'} is ${lane?.state?.process}/${lane?.state?.motion?.value}`,
  }
}
