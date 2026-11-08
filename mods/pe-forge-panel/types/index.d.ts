export type Tab = 'vcycle' | 'sims' | 'checks' | 'gates' | 'parts'

export type Stage = {
  id: string
  title: string
  level: string
  pair: string
  side: 'left' | 'right' | 'bottom'
  status: 'done' | 'active' | 'open' | 'todo'
  attention?: boolean
  facts: string[]
  items: string[]
  issues: string[]
  docs: string[]
  phases: string[]
  ask: string
}

export type VCycle = {
  error?: string
  current?: string
  next?: string
  phase?: string
  order?: string[]
  nodes?: Record<string, Stage>
}

export type Metric = {
  name: string
  unit?: string
  values?: (number | null)[]
  closed_form?: (number | null)[] | number | null
  err_pct?: number | null
  grade?: string
  note?: string
}

export type Chart = {
  type: 'line' | 'bars'
  title?: string
  unit?: string
  x?: { label?: string; unit?: string; values?: number[] }
  y?: { label?: string; unit?: string }
  series?: { name: string; values: (number | null)[] }[]
  items?: { name: string; value: number }[]
}

export type Result = {
  id: string
  title?: string
  module?: string | null
  tool?: string
  date?: string
  record?: string
  file?: string
  grade?: string
  corners?: string[]
  metrics?: Metric[]
  charts?: Chart[]
  notes?: string[]
  problems?: string[]
  stale?: string
}

export type CheckEntry = {
  status: 'pass' | 'warn' | 'fail' | 'skip'
  reason?: string
  findings: { claim?: string; actual?: string; location?: string }[]
}

export type PanelData = {
  ok: boolean
  vcycle?: VCycle
  error?: string
  project?: string
  /** The "maq" mark as block-art rows (forge ui.MAQ_LOGO); empty when [ui] logo = false */
  logo?: string[]
  root?: string
  phase?: string
  scope?: string
  next?: string
  phases?: { id: string; gate: string; title: string; status: string; date?: string; review?: { verdict?: string; status?: string } | null }[]
  modules?: { id: string; name: string; status: string; phases: Record<string, { status: string }> }[]
  results?: Result[]
  results_stamp?: string
  check?: { ok: boolean; at?: string; checks: Record<string, CheckEntry> } | null
  check_stamp?: string
  obligations?: { kind: string; target: string; opened_by?: string }[]
  txn?: { what?: string } | null
  parts?: { at?: string; parts: number; groups: { family: string; footprint?: string; count: number; refs: string[]; need: string }[] } | null
  report?: string
}

declare module 'claude-code' {
  interface PluginState {
    'pe-forge-panel': {
      tab: Shaped<Tab>
      stage: Shaped<string>
      sel: Shaped<number>
      data: Shaped<PanelData | null>
      busy: Shaped<string>
      out: Shaped<string>
    }
  }
}
