import { useCallback, useEffect, useState } from 'react'

import { BackgroundTab } from './components/BackgroundTab'
import { DashboardPage } from './pages/DashboardPage'
import { ManifestBanner } from './components/ManifestBanner'
import { TopBar, type Crumb } from './components/TopBar'
import { SideNav } from './components/SideNav'
import { TicketsPage } from './pages/TicketsPage'
import { RunsPage } from './pages/RunsPage'
import { EscapesPage } from './pages/EscapesPage'
import { MetricsPage } from './pages/MetricsPage'
import { WikiPage } from './pages/WikiPage'
import { SeamsPage } from './pages/SeamsPage'
import { OrchardPage } from './pages/OrchardPage'
import { DeliveryPage } from './pages/DeliveryPage'
import { RequirementsPage } from './pages/RequirementsPage'
import { RequirementPage } from './pages/RequirementPage'
import { TicketPage } from './pages/TicketPage'
import { LanesPage } from './pages/LanesPage'
import { PermissionsPage } from './pages/PermissionsPage'
import { VocabularyPage } from './pages/VocabularyPage'
import { CONTRACT, FactoryProvider, useFactory } from './services/factory'
import { LanesProvider } from './services/lanes'
import { REQUIREMENTS, parse, type Route } from './routes'

import './styles/tokens.css'
import './styles/app.css'

const THEME_KEY = 'factory.theme'

/** F26 — dark by default, light available, remembered. */
function useTheme(): ['dark' | 'light', () => void] {
  const [theme, setTheme] = useState<'dark' | 'light'>(
    () => (localStorage.getItem(THEME_KEY) === 'light' ? 'light' : 'dark'))

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem(THEME_KEY, theme)
  }, [theme])

  return [theme, useCallback(() => setTheme((t) => (t === 'dark' ? 'light' : 'dark')), [])]
}

/** The address bar is the state. `hashchange` is a browser event, not a poll. */
function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parse(location.hash))
  useEffect(() => {
    const onChange = () => setRoute(parse(location.hash))
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}

/**
 * The lane set sits inside the socket provider and outside every surface, because it is read by
 * two of them at once — §4.1's counter in the bar and §4.4's rail on the ticket screen.
 */
export function App() {
  return <FactoryProvider><LanesProvider><Shell /></LanesProvider></FactoryProvider>
}

function Shell() {
  const route = useRoute()
  const [theme, toggleTheme] = useTheme()
  const { versionMismatch } = useFactory()
  const [parent, setParent] = useState<string>()
  const onParent = useCallback((next: string | undefined) => { setParent(next) }, [])

  return (
    <div className="app">
      {/* §4.13 — not a surface: the title and the favicon, for when nobody is looking. */}
      <BackgroundTab />
      <TopBar crumbs={crumbsFor(route, parent)} theme={theme} onTheme={toggleTheme} />

      {/* E2's refusal, on every surface — WEB-FEAT-179. A manifest that did not load empties every
          catalogue and blocks every claim, so the reason belongs in the shell rather than on the
          one page that happens to look emptiest. It renders nothing when the manifest is fine. */}
      <ManifestBanner />

      {/* SRV-FEAT-005 left `hello` as the one frame with no consumer, and asked this ticket to
          use it or delete it. A client whose contract version disagrees with the server's says
          so, loudly, once — and then gets out of the way. */}
      {versionMismatch && (
        <div className="version-warning" data-testid="version-warning">
          This server speaks factory {versionMismatch}; this client was generated
          against {CONTRACT}. Read what follows knowing the two disagree.
        </div>
      )}

      {/* `Shell · Nav` — the design's left rail (`factory.pen` frame `yKLSZ`). The shell holds it
          rather than each page, because the design draws it on every surface and a rail rendered
          per page is a rail that goes missing on whichever page forgot. */}
      <div className="shell">
        <SideNav current={route.name} />
        <main className="shell-main">
      {route.name === 'home' && <DashboardPage />}
      {route.name === 'requirements' && <RequirementsPage />}
      {route.name === 'tickets' && <TicketsPage />}
      {route.name === 'runs' && <RunsPage />}
      {route.name === 'escapes' && <EscapesPage />}
      {route.name === 'metrics' && <MetricsPage />}
      {route.name === 'wiki' && <WikiPage />}
      {route.name === 'seams' && <SeamsPage />}
      {route.name === 'orchard' && <OrchardPage />}
      {route.name === 'delivery' && <DeliveryPage />}
      {route.name === 'requirement' && <RequirementPage key={route.id} id={route.id} />}
      {route.name === 'ticket'
        && <TicketPage key={route.id} id={route.id} tab={route.tab} laneParam={route.lane}
          onParent={onParent} />}
      {route.name === 'lanes' && <LanesPage />}
      {route.name === 'vocabulary' && <VocabularyPage />}
      {route.name === 'permissions' && <PermissionsPage />}
        </main>
      </div>
    </div>
  )
}

/** `requirements / REQ-014 / TKT-142` — `factory.pen`, frame `QFMQY`. */
export function crumbsFor(route: Route, parent?: string): Crumb[] {
  const home: Crumb = {
    label: 'requirements',
    route: route.name === 'requirements' ? undefined : REQUIREMENTS,
  }
  switch (route.name) {
    // §4.18 is where the trail starts, so it has nowhere above it to point at.
    case 'home':
      return [{ label: 'dashboard' }]
    case 'requirements':
      return [home]
    case 'tickets':
      return [{ label: 'tickets' }]
    case 'runs':
      return [{ label: 'runs' }]
    case 'escapes':
      return [{ label: 'escapes' }]
    case 'metrics':
      return [{ label: 'metrics' }]
    case 'wiki':
      return [{ label: 'wiki' }]
    case 'seams':
      return [{ label: 'seams' }]
    case 'orchard':
      return [{ label: 'orchard' }]
    case 'delivery':
      return [{ label: 'delivery' }]
    case 'requirement':
      return [home, { label: route.id }]
    case 'ticket':
      return [
        home,
        ...(parent ? [{ label: parent, route: { name: 'requirement' as const, id: parent } }] : []),
        { label: route.id },
      ]
    case 'vocabulary':
      return [home, { label: 'vocabulary' }]
    case 'permissions':
      return [home, { label: 'permissions' }]
    case 'lanes':
      return [home, { label: 'lanes' }]
  }
}
