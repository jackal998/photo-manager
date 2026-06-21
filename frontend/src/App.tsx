import { cn } from './lib/utils'
import {
  MAIN_EXECUTE_BUTTON,
  MAIN_LANG_TOGGLE,
  MAIN_RESULT_TREE,
  MAIN_SCAN_BUTTON,
  MAIN_SETTINGS_BUTTON,
  MAIN_STATUS_BAR,
} from './testids'

export default function App() {
  return (
    <div className={cn('min-h-screen flex flex-col')}>
      <header className="flex items-center gap-2 border-b px-4 py-2">
        <button data-testid={MAIN_SCAN_BUTTON} className="px-3 py-1 rounded border">
          Scan
        </button>
        <button data-testid={MAIN_EXECUTE_BUTTON} className="px-3 py-1 rounded border">
          Execute
        </button>
        <button data-testid={MAIN_LANG_TOGGLE} className="px-3 py-1 rounded border">
          EN
        </button>
        <button data-testid={MAIN_SETTINGS_BUTTON} className="px-3 py-1 rounded border">
          Settings
        </button>
      </header>

      <main className="flex-1 p-4">
        <div data-testid={MAIN_RESULT_TREE}>
          {/* Result tree populated in 2B */}
        </div>
      </main>

      <footer>
        <p data-testid={MAIN_STATUS_BAR} className="px-4 py-1 text-sm text-muted-foreground">
          Ready
        </p>
      </footer>
    </div>
  )
}
