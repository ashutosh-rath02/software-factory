import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The server owns the data; the client never reads a file and never guesses a port.
// /ws goes through the same proxy, so the browser sees one origin and CORS never applies.
//
// Nothing here names a number. FACTORY_API_PORT is the server this client talks to — the gate
// and the developer both start that server and know its port — and FACTORY_WEB_PORT is where
// this one listens, unset by default so vite takes the first free port it finds. Two sessions on
// this machine used to get the same dev port, the same preview port and the same proxy target,
// so a page served by one reached the other's server and looked perfectly correct (SRV-FIX-027).
//
// A missing API port is fatal rather than defaulted, and only for dev/preview: a build and a
// unit run talk to no server at all, so failing them would be ceremony. A default here is the
// bug itself — it is the sentence "whatever is on that port will do".
export default defineConfig(({ command, mode }) => {
  const api = process.env.FACTORY_API_PORT
  const needsServer = command === 'serve' && mode !== 'test'
  if (needsServer && !api) {
    throw new Error(
      'FACTORY_API_PORT is not set, and this client will not guess which server it is for.\n' +
      '  cd factory/server && ./build.sh run          # prints the port it bound\n' +
      '  FACTORY_API_PORT=<that port> npm run dev\n' +
      'Check /api/health names the repository you meant before believing anything on screen.')
  }
  const proxy = {
    '/api': `http://localhost:${api}`,
    '/ws': { target: `ws://localhost:${api}`, ws: true },
  }
  // Naming no port lets vite walk up from its own default until it finds a free one, so a second
  // session gets a second port instead of the first session's. An explicit FACTORY_WEB_PORT is
  // strict on purpose: someone who names a port wants THAT port, and a quiet move to the next
  // number is how a gate ends up driving a page it did not start.
  const web = process.env.FACTORY_WEB_PORT
  const listen = web ? { port: Number(web), strictPort: true } : {}

  return {
    plugins: [react()],
    server: { ...listen, proxy },
    preview: { ...listen, proxy },
    // The budget EVERY case in this suite runs under, stated here because it has to be one number
    // the whole suite shares. vitest's default is 5 s; three tickets — WEB-FIX-327, WEB-FIX-372
    // and WEB-FIX-389 — are each about a case that mounts the whole app, measures under 2 s alone,
    // and crosses 5 s when the suite runs its files in parallel on a machine holding four lanes.
    // A per-case override answers it for one case and leaves the next one to be filed.
    //
    // 20 s is not room to be slow in: `tests/suite-budget.ts` reads this number back, and the two
    // timing-coupled cases refuse a run that spends more than a stated fraction of it. A budget
    // with no measurement under it is a budget that only ever moves upwards.
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: './src/setupTests.ts',
      testTimeout: 20000,
    },
  }
})
