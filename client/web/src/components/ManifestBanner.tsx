import { RemedyControl } from './Remedy'
import { tone } from './Chips'
import { useFactory } from '../services/factory'
import { TONE } from '../vocabulary'

/**
 * SPEC.md E2's refusal, on every surface — WEB-FEAT-179.
 *
 * A manifest that did not load is the one failure that makes the WHOLE tool unusable rather than
 * one lane: every catalogue is empty, no lane can be claimed, and `GET /api/manifest` holds the
 * only sentence that says why. `services/factory.tsx` fetched that endpoint and kept the success
 * alone, so the most precise refusal this server can produce — the exact JSON pointer that failed,
 * and the key that would have to exist — reached no screen at all. The requirements list carries a
 * `warnings[]` copy of the code, which is `SRV-FIX-071`'s notice about a catalogue and not the
 * state a person is stuck in.
 *
 * The POINTER is rendered verbatim and that is the whole point of the banner. A person acts on it
 * by opening `.factory/factory.json` in their own editor, and a client that reworded, truncated or
 * prettified it would send them to the wrong line of a document this tool may never write.
 *
 * The remedy is `reload_manifest` and never `fix_manifest` (SPEC.md §9.2). Root `CLAUDE.md`: the
 * manifest is "committed, hand-written, and never written by the tool". So the way out is a
 * re-READ — the person edits the file where they already are, and one control asks the server to
 * look again and says whether it is valid now. `tests/refusals.test.tsx` holds the never-writes
 * half to a source scan, because the cheap fix is a textarea and a PUT and it is cheap precisely
 * because it looks like helping.
 */
export function ManifestBanner() {
  const { manifestError, applyManifestAnswer } = useFactory()
  if (!manifestError) return null

  const detail = (manifestError.detail ?? {}) as Record<string, unknown>
  const said = typeof detail.pointer === 'string' ? detail.pointer : ''
  const field = typeof detail.field === 'string' ? detail.field : ''
  const path = typeof detail.path === 'string' ? detail.path : ''

  return (
    <div className="manifest-banner" data-testid="manifest-banner" style={tone(TONE.crash)}>
      <div className="manifest-banner-said" data-testid="manifest-banner-message">
        {manifestError.message ?? `the manifest could not be read (${manifestError.code})`}
      </div>

      {/* VERBATIM, in its own element, so a test can assert the string and not a sentence
          containing it. A pointer inside prose is a pointer a person has to extract by eye. */}
      {said && (
        <div className="manifest-banner-where">
          <code data-testid="manifest-banner-pointer">{said}</code>
          {field && <> — the key is <code data-testid="manifest-banner-field">{field}</code></>}
        </div>
      )}

      {path && (
        <div className="manifest-banner-where">
          in <code data-testid="manifest-banner-path">{path}</code>
        </div>
      )}

      <div className="manifest-banner-how">
        This file is the repository’s: it is committed, hand-written, and never written by this
        tool. Edit it where you already are, then re-read it here.
      </div>

      {/* No remedy on the body, no control on the screen — never a greyed one. The server that
          fills `remedy` in is `SRV-FIX-175`; until it does, this banner is the sentence and the
          pointer, which is already more than any surface showed.

          When it is there, the call is built from the REMEDY (`services/http.ts`) and its answer
          is E2's own body — the manifest, or the same refusal one more time. The provider takes
          it, so a banner that cleared is one the SERVER cleared, off one request and not a clock. */}
      <RemedyControl remedy={manifestError.remedy} onDone={applyManifestAnswer} />
    </div>
  )
}
