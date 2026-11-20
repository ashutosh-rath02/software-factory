import { describe, expect, it } from 'vitest'
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import { join, sep } from 'node:path'

/**
 * WEB-FIX-405 §1 cluster D-NEG — no gate script writes a literal `/tmp` path.
 *
 * A lane's `/tmp` is a read-only tmpfs with one writable subdirectory, which `TMPDIR` names. Every
 * literal `/tmp/...` in this directory is therefore a line that behaves differently inside a lane
 * from outside one, and all three ways that has gone wrong are in the ledger:
 *
 *   · `run-gate.sh:221` could not create its lock at all, and the gate died above its own trap;
 *   · `test-run-gate.sh:126` could not create the lock its case needed, and the case PASSED on the
 *     gate's unrelated failure instead (`WEB-FIX-384`);
 *   · the server the gate starts rooted its agent configuration at `/tmp` and took 27 live checks
 *     down with `confinement_unavailable` (`WEB-FIX-368`).
 *
 * The sweep is DERIVED from `gate/` rather than being a list of three filenames, because the
 * fourth script is the one nobody will remember to add. Comments are stripped first: this file's
 * subject is a path a script WRITES, and the scripts explain the incident in prose that quotes the
 * path it is about.
 */

const GATE = join(__dirname, '..', 'gate')

/** Every script in `gate/` — shell and node alike, read off the directory. */
export function gateScripts(): string[] {
  return readdirSync(GATE).filter((f) => f.endsWith('.sh') || f.endsWith('.mjs')).sort()
}

/**
 * The `/tmp` paths a script would really use, as plain sentences.
 *
 * `${TMPDIR:-/tmp}` is the accepted spelling and the only one: outside a lane it is `/tmp`, inside
 * one it is the lane's own writable scratch, and it is what the three surviving uses in
 * `test-run-gate.sh` already say. Everything else — a bare `/tmp/...`, a `mktemp -d` with no
 * template, a `TMPDIR` default written any other way — is a path that changes meaning in a lane.
 */
export function literalTmpPaths(text: string): string[] {
  const code = text
    .split('\n')
    .map((line) => line.replace(/^\s*#.*$/, '').replace(/^\s*(\*|\/\/|\/\*).*$/, ''))
    .join('\n')
    .replace(/\$\{TMPDIR:-\/tmp\}/g, '${TMPDIR}')
  return [...code.matchAll(/^.*\/tmp\b.*$/gm)].map((m) => m[0].trim())
}

describe('WEB-FIX-405 — the gate scripts and the lane’s read-only /tmp', () => {
  it('the_browser_fixture_keeps_writable_agent_config_outside_the_developer_home', () => {
    const script = readFileSync(join(GATE, 'run-gate.sh'), 'utf8')
    const assignment = /^AGENT_CONFIG_ROOT="\$\(mktemp -d .*\)"$/m.exec(script)?.[0]
    expect(assignment, 'the gate no longer creates a private config directory').toBeTruthy()
    expect(script).toContain('FACTORY_AGENT_CONFIG_ROOT="$AGENT_CONFIG_ROOT"')
    expect(script).toContain('[ -n "$AGENT_CONFIG_ROOT" ] && rm -rf "$AGENT_CONFIG_ROOT"')

    const fixture = mkdtempSync(join(tmpdir(), 'gate-config-test-'))
    const home = join(fixture, 'developer-home')
    const scratch = join(fixture, 'scratch')
    mkdirSync(home)
    mkdirSync(scratch)
    try {
      const made = spawnSync('bash', ['-c', `${assignment}\nprintf '%s' "$AGENT_CONFIG_ROOT"`],
        { encoding: 'utf8', env: { ...process.env, HOME: home, TMPDIR: scratch } })
      expect(made.status, made.stderr).toBe(0)
      expect(made.stdout.startsWith(scratch + sep)).toBe(true)
      expect(made.stdout.startsWith(home + sep)).toBe(false)
    } finally {
      rmSync(fixture, { recursive: true, force: true })
    }
  })

  it('no_gate_script_writes_a_literal_tmp_path', () => {
    const scripts = gateScripts()
    expect(scripts.length, 'no gate script was swept at all').toBeGreaterThan(3)

    const offenders = scripts.flatMap((f) =>
      literalTmpPaths(readFileSync(join(GATE, f), 'utf8')).map((l) => `${f}: ${l}`))
    expect(offenders, `these lines write a path a lane cannot use:\n  ${offenders.join('\n  ')}`)
      .toEqual([])
  })

  /**
   * NEGATIVE (mandatory). The predicate above is a regex over text, so the thing that can rot is
   * its ability to see anything. Driven on both spellings: the one the ledger is about must be
   * named, and the one the scripts are allowed to use must not.
   */
  it('the_tmp_sweep_can_still_see_a_literal_path', () => {
    expect(literalTmpPaths('LOCK="/tmp/factory-browser-gate.lock"\n'),
      'the rule no longer sees the exact line WEB-FIX-368 was filed about').toHaveLength(1)
    expect(literalTmpPaths('L="$(mktemp -u "${TMPDIR:-/tmp}/web-fix-405.XXXXXX")"\n'),
      'the accepted spelling was refused, which forbids the fix').toEqual([])
    expect(literalTmpPaths('# the lock used to live at /tmp/factory-browser-gate.lock\n'),
      'a comment recording the incident was read as a path').toEqual([])
  })
})
