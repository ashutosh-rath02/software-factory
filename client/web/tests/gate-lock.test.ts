import { describe, expect, it } from 'vitest'
import { execFileSync, spawn } from 'node:child_process'
import { existsSync, mkdtempSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

/**
 * WEB-FIX-405 §1 cluster D — the browser gate's one-Chrome lock lives where a lane can write it,
 * and it is still ONE lock for the whole machine's worth of lanes.
 *
 * `run-gate.sh` opened `/tmp/factory-browser-gate.lock` unconditionally. Inside a lane `/tmp` is a
 * read-only tmpfs (`confine.cpp` — `--tmpfs /tmp` … `--remount-ro /tmp`), so `exec 9>"$LOCK"` is a
 * shell redirection failure on line nine of the script and no client ticket's GATE stage could run
 * its own tier 2b at all (`WEB-FIX-368`, `WEB-FIX-376`).
 *
 * The obvious repair is the one this file exists to refuse. `FACTORY_GATE_LOCK=$TMPDIR/…` makes
 * the gate run, and `$TMPDIR` inside a lane is that lane's OWN private tmpfs — so every lane takes
 * an uncontended lock and *one Chrome at a time on this machine* silently becomes *one Chrome per
 * lane, all at once*. `docs/operations/ESCAPES.md` carries that exact sentence twice on 2026-08-22, and
 * `WEB-FIX-037` is the ticket whose guarantee it quietly spends: five parallel client lanes were
 * each told not to run the gate and four escaped a defect apiece.
 *
 * So the two halves are asserted TOGETHER and neither alone is the criterion: the path must be
 * writable where a lane runs, and it must be the SAME FILE for two checkouts of one repository.
 *
 * Every case drives `gate/gate-lock.sh` — the resolver `run-gate.sh` itself uses. A resolver
 * re-implemented here would be a test of this file.
 */

const CLIENT = join(__dirname, '..')
const RESOLVER = join(CLIENT, 'gate', 'gate-lock.sh')

/**
 * The product's own answer for a directory, or the refusal it printed.
 *
 * `env` is how a LANE is simulated, and it is not decoration. Two lanes differ in exactly the
 * places the confinement gives each one its own mount: `TMPDIR` (`/tmp/factory`, a fresh private
 * tmpfs per lane), `HOME` (`/home/lane`, likewise) and `CLAUDE_CONFIG_DIR`. Resolving both
 * worktrees with ONE environment is resolving them as one lane — which is a fixture in which a
 * per-lane path and a shared path are the same string, and every comparison below passes on both.
 */
function resolve(dir: string, env: Record<string, string> = {}):
    { ok: boolean; path: string; said: string } {
  try {
    const out = execFileSync('bash', [RESOLVER, dir],
      { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env, ...env } })
    return { ok: true, path: out.trim(), said: '' }
  } catch (e) {
    const err = e as { status?: number; stdout?: string; stderr?: string }
    return { ok: false, path: (err.stdout ?? '').trim(), said: `${err.stdout ?? ''}${err.stderr ?? ''}` }
  }
}

/** Two paths are ONE lock when they are one inode. `flock` locks inodes, not names. */
function sameLock(a: string, b: string): boolean {
  if (!existsSync(a) || !existsSync(b)) return false
  const [x, y] = [statSync(a), statSync(b)]
  return x.dev === y.dev && x.ino === y.ino
}

/** A scratch repository with two worktrees — two lanes, structurally, of one repository. */
function twoLanes(): { root: string; a: string; b: string; cleanup: () => void } {
  const root = mkdtempSync(join(tmpdir(), 'web-fix-405-lanes-'))
  const repo = join(root, 'repo')
  const git = (cwd: string, ...args: string[]) =>
    execFileSync('git', ['-c', 'user.name=Gate', '-c', 'user.email=gate@example.invalid', ...args],
      { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })
  execFileSync('git', ['init', '-b', 'main', repo], { stdio: 'ignore' })
  writeFileSync(join(repo, 'README.md'), 'a repository\n')
  git(repo, 'add', '-A')
  git(repo, 'commit', '-m', 'the repository as it stands', '--no-gpg-sign')
  git(repo, 'worktree', 'add', '-b', 'ticket/one', join(root, 'lane-a'))
  git(repo, 'worktree', 'add', '-b', 'ticket/two', join(root, 'lane-b'))
  return {
    root, a: join(root, 'lane-a'), b: join(root, 'lane-b'),
    cleanup: () => rmSync(root, { recursive: true, force: true }),
  }
}

describe('WEB-FIX-405 — the browser gate’s lock', () => {
  it('the_gate_takes_a_lock_a_lane_can_write', () => {
    const answer = resolve(CLIENT)
    expect(answer.ok, `the resolver refused this checkout: ${answer.said}`).toBe(true)
    expect(answer.path, 'the resolver printed nothing').not.toEqual('')

    // Not /tmp, and not the lane's private scratch either — the two paths a lane cannot use for
    // this and the two the old default and its workaround used.
    expect(answer.path, 'the lock is back in a lane’s read-only /tmp').not.toMatch(/^\/tmp\//)
    const scratch = process.env.TMPDIR
    if (scratch) {
      expect(answer.path.startsWith(scratch),
        `the lock is inside TMPDIR (${scratch}), which is one lane's own tmpfs`).toBe(false)
    }

    // And it is a path that can actually be OPENED FOR WRITING — asserted by doing it, because
    // "it looks writable" is what `WEB-FIX-384` found passing on a lock that never existed, and
    // `Read-only file system` on this exact redirection is the whole of `WEB-FIX-368`.
    //
    // Opened, not locked. This suite is a phase of `npm run gate`, and the gate is holding this
    // very lock while the phase runs — a case that asked for it here would be a case that can only
    // pass when nobody is gating. Whether the file can be LOCKED, and by whom, is
    // `two_lanes_queue_for_one_browser`, on a scratch repository of its own.
    execFileSync('bash', ['-c', `: >> "${answer.path}"`], { stdio: 'ignore' })
    expect(existsSync(answer.path), 'the resolved lock file could not be created')
      .toBe(true)

    const lanes = twoLanes()
    try {
      const scratch = resolve(lanes.a)
      expect(scratch.ok, `a fresh checkout could not resolve a lock: ${scratch.said}`).toBe(true)
      const held = execFileSync('bash',
        ['-c', `exec 9>"${scratch.path}"; if flock -n 9; then echo GOT; else echo BUSY; fi`],
        { encoding: 'utf8' }).trim()
      expect(held, 'the resolved path exists and cannot be locked, so the queue has no door')
        .toBe('GOT')
    } finally {
      lanes.cleanup()
    }
  }, 60000)

  it('two_lanes_queue_for_one_browser', async () => {
    const lanes = twoLanes()
    // Each worktree is resolved with its OWN scratch and home, because that is what makes two
    // lanes two lanes. Resolved with one environment, `$TMPDIR/factory-browser-gate.lock` and the
    // repository-wide path are the same string and this case passes on the defect it exists for —
    // measured: the private-lock break left it green until these two lines were added.
    const scratchA = mkdtempSync(join(tmpdir(), 'web-fix-405-lane-a-'))
    const scratchB = mkdtempSync(join(tmpdir(), 'web-fix-405-lane-b-'))
    try {
      const a = resolve(lanes.a, { TMPDIR: scratchA, HOME: scratchA })
      const b = resolve(lanes.b, { TMPDIR: scratchB, HOME: scratchB })
      expect(a.ok && b.ok, `a lane could not resolve a lock: ${a.said}${b.said}`).toBe(true)
      expect(scratchA, 'the two lanes were given the same scratch, so they are one lane')
        .not.toEqual(scratchB)

      execFileSync('bash', ['-c', `exec 9>"${a.path}" && flock -n 9`], { stdio: 'ignore' })
      execFileSync('bash', ['-c', `exec 9>"${b.path}" && flock -n 9`], { stdio: 'ignore' })
      expect(sameLock(a.path, b.path),
        `two worktrees of one repository resolved to two different locks:\n  ${a.path}\n  ${b.path}`)
        .toBe(true)

      // Two callers RELEASED TOGETHER, never a loop: a sequential probe cannot tell a lock from a
      // counter. Lane A holds it; lane B asks for it and must not get it; A lets go and B does.
      const holder = spawn('bash', ['-c', `exec 9>"${a.path}"; flock 9; echo held; sleep 30`],
        { stdio: ['ignore', 'pipe', 'ignore'] })
      try {
        await new Promise<void>((done) => holder.stdout!.once('data', () => done()))

        const blocked = execFileSync('bash',
          ['-c', `exec 9>"${b.path}"; if flock -w 1 9; then echo GOT; else echo QUEUED; fi`],
          { encoding: 'utf8' }).trim()
        expect(blocked, 'lane B took the browser lock while lane A was holding it — the two lanes '
          + 'have a lock each, which is no lock at all').toBe('QUEUED')

        holder.kill('SIGKILL')
        await new Promise<void>((done) => holder.once('exit', () => done()))

        const after = execFileSync('bash',
          ['-c', `exec 9>"${b.path}"; if flock -w 5 9; then echo GOT; else echo QUEUED; fi`],
          { encoding: 'utf8' }).trim()
        expect(after, 'the lock was not released when its holder died, so one wedged gate means no '
          + 'gate ever again').toBe('GOT')
      } finally {
        holder.kill('SIGKILL')
      }
    } finally {
      lanes.cleanup()
      rmSync(scratchA, { recursive: true, force: true })
      rmSync(scratchB, { recursive: true, force: true })
    }
  }, 60000)

  /**
   * MANDATORY NEGATIVE. `sameLock` is the whole of the case above, so it is the thing that has to
   * be shown capable of answering NO — on exactly the path the workaround used, a per-lane scratch
   * directory, which is how this defect gets closed a second time by somebody in a hurry.
   */
  it('a_lock_private_to_one_lane_is_not_a_lock', () => {
    const lanes = twoLanes()
    try {
      const privateA = join(lanes.a, 'factory-browser-gate.lock')
      const privateB = join(lanes.b, 'factory-browser-gate.lock')
      writeFileSync(privateA, '')
      writeFileSync(privateB, '')
      expect(sameLock(privateA, privateB),
        'two per-lane files were judged to be one lock, so the case above cannot fail').toBe(false)

      const shared = resolve(lanes.a)
      execFileSync('bash', ['-c', `exec 9>"${shared.path}" && flock -n 9`], { stdio: 'ignore' })
      expect(sameLock(shared.path, shared.path), 'and one file is not judged to be two').toBe(true)

      // The product must never ANSWER with a per-lane path, whatever the environment says. This is
      // the workaround `WEB-FIX-376` §1 records, run against the resolver itself.
      const scratch = mkdtempSync(join(tmpdir(), 'web-fix-405-scratch-'))
      try {
        const underAWorkaround = execFileSync('bash', [RESOLVER, lanes.a],
          { encoding: 'utf8', env: { ...process.env, TMPDIR: scratch } }).trim()
        expect(underAWorkaround.startsWith(scratch),
          'the resolver put the lock in TMPDIR, which is one lane’s private tmpfs').toBe(false)
      } finally {
        rmSync(scratch, { recursive: true, force: true })
      }
    } finally {
      lanes.cleanup()
    }
  }, 60000)
})
