import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { Chart, CheckEntry, PanelData, Result, Stage, Tab } from '../types'

// pe-forge results beside the chat. All data comes from `forge panel --json` (the project's own files); every button
// runs a forge command in the project folder. Opens by itself when a new simulation result or a new full check lands.
const PANE = 'pe-forge-results'
const RED = '#d52b3d'
const GREEN = '#3fb950'
const AMBER = '#d29922'
const PINK = '#ff6e9c'
const CHART_W = 40

const tab = atom({ plugin: 'pe-forge-panel', key: 'tab' } as const, 'vcycle' as Tab, { shape: 'tab-2' })
const stage = atom({ plugin: 'pe-forge-panel', key: 'stage' } as const, '', { shape: 'stage-1' })
const sel = atom({ plugin: 'pe-forge-panel', key: 'sel' } as const, 0, { shape: 'sel-1' })
const data = atom({ plugin: 'pe-forge-panel', key: 'data' } as const, null as PanelData | null, { shape: 'data-1' })
const busy = atom({ plugin: 'pe-forge-panel', key: 'busy' } as const, '', { shape: 'busy-1' })
const out = atom({ plugin: 'pe-forge-panel', key: 'out' } as const, '', { shape: 'out-1' })

let cwd = ''
let forgeBin = ''
let seenResults: string | undefined
let seenCheck: string | undefined

async function locateForge($: any): Promise<string> {
  if (forgeBin) return forgeBin
  const pe = await $.env.get('PE_FORGE')
  const home = await $.env.get('HOME')
  const cands = [pe ? `${pe}/bin/forge` : '', home ? `${home}/.local/bin/forge` : '', 'forge'].filter(Boolean)
  for (const c of cands) {
    try {
      const r = await $.process.run([c, 'panel', '--json'], { cwd, timeoutMs: 30000 })
      if (r.exitCode === 0 && r.stdout.trim().startsWith('{')) {
        forgeBin = c
        return c
      }
    } catch {
      // not there: try the next one
    }
  }
  return ''
}

async function forge($: any, args: string[], timeoutMs = 120000) {
  const bin = await locateForge($)
  if (!bin) return { exitCode: 127, stdout: '', stderr: 'forge not found (set PE_FORGE, or run bootstrap.sh)' }
  try {
    return await $.process.run([bin, ...args], { cwd, timeoutMs })
  } catch (err) {
    return { exitCode: 1, stdout: '', stderr: String(err) }
  }
}

async function refresh($: any): Promise<PanelData | null> {
  const r = await forge($, ['panel', '--json'], 30000)
  let d: PanelData | null = null
  try {
    d = JSON.parse(r.stdout) as PanelData
  } catch {
    d = { ok: false, error: (r.stderr || 'no answer from forge panel').slice(0, 300) }
  }
  await update($, data, () => d)
  return d
}

async function run($: any, label: string, args: string[], after?: Tab) {
  await update($, busy, () => `${label}…`)
  const r = await forge($, args, 600000)
  const text = (r.stdout + (r.stderr ? `\n${r.stderr}` : '')).trim()
  await update($, out, () => `$ forge ${args.join(' ')}\n${text}`.split('\n').slice(0, 40).join('\n'))
  await refresh($)
  if (after) await update($, tab, () => after)
  await update($, busy, () => '')
}

async function openPath($: any, path: string) {
  for (const opener of ['open', 'xdg-open']) {
    try {
      const r = await $.process.run([opener, path], { cwd, timeoutMs: 10000 })
      if (r.exitCode === 0) return
    } catch {
      // try the next opener
    }
  }
  await update($, out, () => `could not open ${path}`)
}

// ------------------------------------------------------------------ drawing helpers
const BLOCKS = '▁▂▃▄▅▆▇█'

function resample(vals: number[], w: number): number[] {
  if (vals.length === 0) return []
  if (vals.length === 1) return Array(w).fill(vals[0])
  return Array.from({ length: w }, (_, i) => {
    const x = (i / (w - 1)) * (vals.length - 1)
    const k = Math.floor(x)
    const f = x - k
    return k + 1 < vals.length ? vals[k] * (1 - f) + vals[k + 1] * f : vals[k]
  })
}

function spark(vals: number[], lo: number, hi: number, w: number): string {
  const span = hi - lo || 1
  return resample(vals, w)
    .map(v => BLOCKS[Math.max(0, Math.min(7, Math.round(((v - lo) / span) * 7)))])
    .join('')
}

const fmt = (v: number | null | undefined) =>
  v === null || v === undefined ? '–' : Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toPrecision(3)
const pad = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + '…' : s.padEnd(n))
const gradeColor = (g?: string) => (g === 'ACCEPTED' ? GREEN : g === 'PROVISIONAL' ? AMBER : g === 'REJECTED' ? PINK : undefined)
const gradeIcon = (g?: string) => (g === 'ACCEPTED' ? '✓' : g === 'PROVISIONAL' ? '?' : g === 'REJECTED' ? '✗' : '–')
const statusIcon = (s: string) => (s === 'passed' ? '✓' : s === 'active' ? '▶' : s === 'in-review' ? '?' : s === 'skipped' ? '–' : '·')

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    cwd = e.cwd
    await $.command.register({
      name: 'results',
      description: 'Show the pe-forge results panel (simulations, checks, gates, parts)',
      argumentHint: '[pe-forge project folder]',
    })
    const d = await refresh($)
    if (d?.ok) {
      seenResults = d.results_stamp
      seenCheck = d.check_stamp
      if ((d.results ?? []).length > 0) void $.ui.open({ id: PANE, title: 'pe-forge results' })
    }
    return next(e)
  })

  on('command.run', { command: 'results' }, async ($, e) => {
    const arg = e.args.trim().replace(/^~(?=\/)/, (await $.env.get('HOME')) ?? '~')
    if (arg) {
      cwd = arg                      // look at another pe-forge project than the session's folder
      seenResults = undefined
      seenCheck = undefined
    }
    const d = await refresh($)
    if (!d?.ok) return { text: `pe-forge panel: ${d?.error ?? 'not inside a pe-forge project'}` }
    await $.ui.open({ id: PANE, title: 'pe-forge results' })
    seenResults = d.results_stamp
    seenCheck = d.check_stamp
    return { text: `pe-forge results panel opened for ${d.project} (keys 1-5 switch tabs).` }
  })

  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    const d = await refresh($)
    if (d?.ok) {
      if (seenResults !== undefined && d.results_stamp !== seenResults && (d.results ?? []).length > 0) {
        await update($, tab, () => 'sims')
        await update($, sel, () => 0)
        void $.ui.open({ id: PANE, title: 'pe-forge results' })
      } else if (seenCheck !== undefined && d.check_stamp && d.check_stamp !== seenCheck) {
        await update($, tab, () => 'checks')
        void $.ui.open({ id: PANE, title: 'pe-forge results' })
      }
      seenResults = d.results_stamp
      seenCheck = d.check_stamp
    }
    return done
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button, Select } = $.ui.resolve(e)
    const d = await read($, data)
    const t = await read($, tab)
    const working = await read($, busy)
    const output = await read($, out)

    if (!d || !d.ok) {
      return (
        <Box flexDirection="column">
          <Text color={RED} bold italic>maq</Text>
          <Text dimColor>{d?.error ?? 'Loading…'}</Text>
          <Button label="Retry" hotkey="u" onPress={() => void refresh($)} />
        </Box>
      )
    }

    const tabs: [Tab, string, string][] = [['vcycle', '1', 'V-cycle'], ['sims', '2', `Simulations (${(d.results ?? []).length})`],
      ['checks', '3', 'Checks'], ['gates', '4', 'Gates'], ['parts', '5', 'Parts']]

    const header = (
      <Box flexDirection="column">
        <Box flexDirection="row" gap={1}>
          <Text color={RED} bold italic>maq</Text>
          <Text color={RED}>▸</Text>
          <Text bold>{d.project}</Text>
          <Text dimColor>· {d.phase} · next: {d.next}</Text>
        </Box>
        <Box flexDirection="row" gap={1}>
          {tabs.map(([id, key, label]) => (
            <Button key={`tab-${id}`} hotkey={key} label={id === t ? `[${label}]` : ` ${label} `}
              onPress={() => void update($, tab, () => id)} />
          ))}
        </Box>
        {working ? <Text color={AMBER}>{working}</Text> : null}
      </Box>
    )

    let body = null
    if (t === 'vcycle') body = await vTab($, d, Box, Text, Button)
    if (t === 'sims') body = await simsTab($, d, Box, Text, Button, Select)
    if (t === 'checks') body = checksTab($, d, Box, Text, Button)
    if (t === 'gates') body = gatesTab($, d, Box, Text, Button)
    if (t === 'parts') body = partsTab($, d, Box, Text, Button)

    return (
      <Box flexDirection="column" gap={1}>
        {header}
        {body}
        {output && t !== 'sims' ? (
          <Box flexDirection="column">
            {output.split('\n').slice(0, 18).map((l, i) => <Text key={`o${i}`} dimColor>{l}</Text>)}
          </Box>
        ) : null}
      </Box>
    )
  })
}

// ------------------------------------------------------------------ tabs
const V_ROWS: [string, string][] = [['concept', 'validation'], ['sysreq', 'sysver'], ['arch', 'integration'],
  ['design', 'block'], ['build', 'unit']]
const V_W = 62

function stageMark(s: Stage, current: string): [string, string | undefined] {
  if (s.id === current) return ['▶', RED]
  if (s.attention) return ['⚠', AMBER]
  if (s.status === 'done') return ['✓', GREEN]
  if (s.status === 'open') return ['·', AMBER]
  return ['○', undefined]
}

async function vTab($: any, d: PanelData, Box: any, Text: any, Button: any) {
  const v = d.vcycle
  if (!v || v.error || !v.nodes) return <Text color={PINK}>V-cycle unavailable: {v?.error ?? 'no data'}</Text>
  const nodes = v.nodes
  const current = v.current ?? 'concept'
  const chosen = (await read($, stage)) || current
  const s = nodes[chosen] ?? nodes[current]
  const label = (id: string) => {
    const [m] = stageMark(nodes[id], current)
    return `${m} ${nodes[id].title}`
  }
  const btn = (id: string) => {
    const [, color] = stageMark(nodes[id], current)
    return (
      <Button key={`v-${id}`} label={id === chosen ? `[${label(id)}]` : label(id)}
        onPress={() => void update($, stage, () => id)}>
        <Text color={color} bold={id === current || id === chosen}>{id === chosen ? `[${label(id)}]` : label(id)}</Text>
      </Button>
    )
  }
  const pair = nodes[s.pair]
  return (
    <Box flexDirection="column" gap={1}>
      <Box flexDirection="column">
        <Text dimColor>{pad('  definition', V_W - 14)}verification</Text>
        {V_ROWS.map(([l, r], i) => {
          const fill = Math.max(2, V_W - i * 4 - label(l).length - label(r).length - 4)
          return (
            <Box key={`vr${i}`} flexDirection="row" marginLeft={i * 2}>
              {btn(l)}
              <Text dimColor> {'─'.repeat(fill)} </Text>
              {btn(r)}
              {l === current || r === current ? <Text color={RED}> ◀ now</Text> : null}
            </Box>
          )
        })}
        <Text dimColor>{'  '}✓ done  ▶ now  ⚠ needs attention  · started  ○ not started   ·   {v.phase}: {v.next}</Text>
      </Box>
      <Box flexDirection="column">
        <Text bold color={RED}>{s.title} <Text dimColor>· {s.level} {s.side === 'left' ? 'definition' : s.side === 'right' ? 'verification' : 'build / unit'} · {s.phases.join(', ')} · {s.id === current ? 'working here now' : s.status}</Text></Text>
        {s.facts.map((f, k) => <Text key={`fa${k}`}>{f}</Text>)}
        {s.items.length ? s.items.slice(0, 8).map((it, k) => <Text key={`it${k}`} dimColor>  • {it}</Text>) : null}
        {s.issues.map((f, k) => <Text key={`is${k}`} color={AMBER}>⚠ {f}</Text>)}
        {pair ? <Text dimColor>{s.side === 'left' ? 'Checked by' : s.side === 'right' ? 'Checks' : 'Pairs with'}: {pair.title} ({pair.level})</Text> : null}
        {s.docs.length ? <Text dimColor>Docs: {s.docs.slice(0, 4).join(' · ')}</Text> : null}
      </Box>
      <Box flexDirection="row" gap={1}>
        <Button label={`Ask Claude about ${s.title}`} hotkey="a"
          onPress={() => void $.prompt.fill({ text: s.ask, mode: 'replace' })} />
        <Button label="Ask now" hotkey="n" onPress={() => void $.prompt.submit({ text: s.ask })} />
        {pair ? <Button label={`Go to ${pair.title}`} hotkey="p" onPress={() => void update($, stage, () => pair.id)} /> : null}
        {s.docs.length ? <Button label="Open doc" hotkey="o" onPress={() => void openPath($, `${d.root}/${s.docs[0]}`)} /> : null}
        <Button label="Back to now" hotkey="b" onPress={() => void update($, stage, () => current)} />
      </Box>
    </Box>
  )
}

async function simsTab($: any, d: PanelData, Box: any, Text: any, Button: any, Select: any) {
  const list = d.results ?? []
  if (list.length === 0) {
    return <Text dimColor>No simulation results yet. They appear here when a simulation finishes.</Text>
  }
  const i = Math.min(await read($, sel), list.length - 1)
  const r: Result = list[i]
  const corners = r.corners ?? []
  const cw = 8
  return (
    <Box flexDirection="column" gap={1}>
      {list.length > 1 ? (
        <Select label="Result " value={String(i)}
          options={list.map((x, k) => ({ value: String(k), label: `${gradeIcon(x.grade)} ${x.title ?? x.id}` }))}
          onSelect={(v: string) => void update($, sel, () => Number(v))} />
      ) : null}
      <Box flexDirection="column">
        <Text bold>{r.title ?? r.id}</Text>
        <Text dimColor>{r.module ?? 'system'} · {r.tool} · {(r.date ?? '').replace('T', ' ').slice(0, 16)}</Text>
        <Text color={gradeColor(r.grade)}>{gradeIcon(r.grade)} {r.grade}</Text>
        {r.stale ? <Text color={AMBER}>⚠ {r.stale}</Text> : null}
        {(r.problems ?? []).map((p, k) => <Text key={`p${k}`} color={PINK}>✗ {p}</Text>)}
      </Box>
      {(r.metrics ?? []).length ? (
        <Box flexDirection="column">
          <Text dimColor>{pad('Result', 22)}{corners.map(c => pad(c, cw)).join('')}{pad('', 5)}vs calc</Text>
          {(r.metrics ?? []).map((m, k) => (
            <Box key={`m${k}`} flexDirection="row">
              <Text>{pad(m.name, 22)}{(m.values ?? []).map(v => pad(fmt(v), cw)).join('')}{pad(m.unit ?? '', 5)}</Text>
              <Text dimColor>{m.err_pct === null || m.err_pct === undefined ? pad('no calc', 8) : pad(`${m.err_pct > 0 ? '+' : ''}${m.err_pct.toFixed(1)} %`, 8)}</Text>
              <Text color={gradeColor(m.grade)}>{gradeIcon(m.grade)}</Text>
            </Box>
          ))}
        </Box>
      ) : null}
      {(r.charts ?? []).map((c, k) => chart(c, k, Box, Text))}
      {(r.notes ?? []).length ? (
        <Box flexDirection="column">
          {(r.notes ?? []).map((n, k) => <Text key={`n${k}`} dimColor>• {n}</Text>)}
        </Box>
      ) : null}
      <Box flexDirection="row" gap={1}>
        {r.record ? <Button label="Open record" hotkey="o" onPress={() => void openPath($, `${d.root}/${r.record}`)} /> : null}
        <Button label="Full report ↗" hotkey="r" onPress={() => void run($, 'Writing the report', ['report', '--open'])} />
        <Button label="Refresh" hotkey="u" onPress={() => void refresh($)} />
      </Box>
    </Box>
  )
}

function chart(c: Chart, k: number, Box: any, Text: any) {
  if (c.type === 'bars') {
    const items = c.items ?? []
    const top = Math.max(...items.map(it => it.value), 0) || 1
    const total = items.reduce((s, it) => s + it.value, 0)
    return (
      <Box key={`c${k}`} flexDirection="column">
        <Text bold>{c.title} <Text dimColor>({fmt(total)} {c.unit ?? ''} total)</Text></Text>
        {items.map((it, j) => (
          <Box key={`b${k}-${j}`} flexDirection="row">
            <Text dimColor>{pad(it.name, 17)}</Text>
            <Text color={RED}>{'█'.repeat(Math.max(1, Math.round((it.value / top) * 22)))}</Text>
            <Text> {fmt(it.value)} {c.unit ?? ''}</Text>
          </Box>
        ))}
      </Box>
    )
  }
  const xs = c.x?.values ?? []
  const series = (c.series ?? []).map(s => ({ name: s.name, vals: s.values.filter((v): v is number => v !== null) }))
  const all = series.flatMap(s => s.vals)
  if (!all.length) return null
  const lo = Math.min(...all)
  const hi = Math.max(...all)
  const colors = [RED, '#5aa9ff', GREEN, AMBER]
  return (
    <Box key={`c${k}`} flexDirection="column">
      <Text bold>{c.title}</Text>
      {series.map((s, j) => (
        <Box key={`s${k}-${j}`} flexDirection="row">
          <Text dimColor>{pad(s.name, 7)}</Text>
          <Text color={colors[j % colors.length]}>{spark(s.vals, lo, hi, CHART_W)}</Text>
          <Text dimColor> {fmt(Math.min(...s.vals))}–{fmt(Math.max(...s.vals))} {c.y?.unit ?? ''}</Text>
        </Box>
      ))}
      {xs.length ? (
        <Text dimColor>{pad('', 7)}{pad(`${fmt(xs[0])}`, CHART_W - 8)}{fmt(xs[xs.length - 1])} {c.x?.unit ?? ''}</Text>
      ) : null}
    </Box>
  )
}

function checksTab($: any, d: PanelData, Box: any, Text: any, Button: any) {
  const ck = d.check
  const icon: Record<CheckEntry['status'], [string, string | undefined]> = {
    pass: ['✓', GREEN], warn: ['!', AMBER], fail: ['✗', PINK], skip: ['–', undefined],
  }
  return (
    <Box flexDirection="column" gap={1}>
      {ck ? (
        <Box flexDirection="column">
          <Text>forge check · {(ck.at ?? '').replace('T', ' ')} · <Text color={ck.ok ? GREEN : PINK} bold>{ck.ok ? 'OK' : 'FAILED'}</Text></Text>
          {Object.entries(ck.checks).map(([name, c]) => (
            <Box key={`k-${name}`} flexDirection="column">
              <Text>
                <Text color={icon[c.status][1]}>{icon[c.status][0]} </Text>
                {pad(name, 15)}
                <Text dimColor>{c.status === 'skip' ? `skipped: ${c.reason ?? ''}` : c.reason ?? ''}</Text>
              </Text>
              {c.status === 'fail' || c.status === 'warn'
                ? c.findings.slice(0, 2).map((f, j) => (
                  <Text key={`f-${name}-${j}`} dimColor>    {f.location ? `${f.location}: ` : ''}{f.claim}, {f.actual}</Text>
                ))
                : null}
            </Box>
          ))}
        </Box>
      ) : (
        <Text dimColor>No full `forge check` yet.</Text>
      )}
      {(d.obligations ?? []).length ? (
        <Box flexDirection="column">
          <Text bold>Open re-checks (they block finishing)</Text>
          {(d.obligations ?? []).map((o, j) => (
            <Text key={`ob${j}`} color={AMBER}>⚠ {o.kind}:{o.target} <Text dimColor>({o.opened_by})</Text></Text>
          ))}
        </Box>
      ) : null}
      {d.txn ? <Text color={AMBER}>Transaction open: {d.txn.what}</Text> : null}
      <Box flexDirection="row" gap={1}>
        <Button label="Run forge check" hotkey="c" onPress={() => void run($, 'Running forge check', ['check'], 'checks')} />
        <Button label="Full report ↗" hotkey="r" onPress={() => void run($, 'Writing the report', ['report', '--open'])} />
      </Box>
    </Box>
  )
}

function gatesTab($: any, d: PanelData, Box: any, Text: any, Button: any) {
  const gate = d.phase && /^P\d$/.test(d.phase) ? `G${d.phase.slice(1)}` : ''
  const args = gate ? ['gate', 'check', gate, ...(d.scope && d.scope !== 'system' ? ['--module', d.scope] : [])] : []
  return (
    <Box flexDirection="column" gap={1}>
      <Box flexDirection="column">
        {(d.phases ?? []).map(p => (
          <Text key={`ph-${p.id}`} color={p.status === 'passed' ? GREEN : p.status === 'active' ? RED : undefined}
            dimColor={p.status === 'todo'}>
            {statusIcon(p.status)} {p.id} {pad(p.title, 34)} {p.status === 'passed' ? `${p.gate} ${p.date ?? ''}` : ''}
            {p.review?.verdict ? ` · ${p.review.verdict} ${p.review.status ?? ''}` : ''}
          </Text>
        ))}
      </Box>
      {(d.modules ?? []).length ? (
        <Box flexDirection="column">
          <Text bold>Modules</Text>
          {(d.modules ?? []).map(m => (
            <Text key={`mo-${m.id}`}>
              {statusIcon(m.status)} {m.id} {pad(m.name, 20)}
              <Text dimColor>{Object.entries(m.phases).map(([k, v]) => `${k}${statusIcon(v.status)}`).join(' ')}</Text>
            </Text>
          ))}
        </Box>
      ) : null}
      <Text>Next: {d.next}</Text>
      {gate ? (
        <Button label={`Check ${gate}${args.includes('--module') ? ` (${d.scope})` : ''}`} hotkey="g"
          onPress={() => void run($, `Checking ${gate}`, args, 'gates')} />
      ) : null}
    </Box>
  )
}

function partsTab($: any, d: PanelData, Box: any, Text: any, Button: any) {
  const p = d.parts
  return (
    <Box flexDirection="column" gap={1}>
      {p ? (
        p.groups.length ? (
          <Box flexDirection="column">
            <Text>{p.parts} placed part(s) in {p.groups.length} family / families still need datasheet work</Text>
            {p.groups.slice(0, 14).map((g, j) => (
              <Text key={`g${j}`}>
                <Text color={AMBER}>• </Text>{pad(g.family, 26)}<Text dimColor>{g.count}× {g.refs.slice(0, 6).join(', ')} · {g.need}</Text>
              </Text>
            ))}
          </Box>
        ) : (
          <Text color={GREEN}>✓ Every placed part has its datasheet, its check and status Active.</Text>
        )
      ) : (
        <Text dimColor>No work list yet.</Text>
      )}
      <Box flexDirection="row" gap={1}>
        <Button label="Refresh work list" hotkey="t" onPress={() => void run($, 'Reading the board', ['evidence', 'todo', '--json'], 'parts')} />
        <Button label="Copy datasheets into project" hotkey="s" onPress={() => void run($, 'Copying datasheets', ['evidence', 'sync'], 'parts')} />
      </Box>
    </Box>
  )
}
