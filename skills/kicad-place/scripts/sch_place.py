#!/usr/bin/env python3
"""Place MC_Parts symbols (unwired) on a KiCad 10 schematic sheet from a JSON spec.

Usage:
  sch_place.py <sheet.kicad_sch> <spec.json>            # dry run: writes <scratch>/<sheet> + PNG render, checks
  sch_place.py <sheet.kicad_sch> <spec.json> --apply    # same checks, then backs up and writes the real sheet
  sch_place.py <sheet.kicad_sch> --pins MC_ID X Y ROT   # print the schematic pin coordinates a part would get

Spec (JSON):
  {"parts": [ {"id": "IC-0021", "ref": "U1204", "x": 90.17, "y": 213.36, "rot": 0},
              {"id": "CAP-0036", "ref": "C1226", "x": 72.39, "y": 201.93, "rot": 0,
               "dnp": false, "side": "R"} ...]}
  x / y in mm on the 1.27 mm grid; rot 0/90/180/270. side (passives only) puts Reference/Value:
  R right (default), L left, B below-right (rot 0); U ref above / value below (default), A both above,
  D both below (rot 90). Optional "ref_xy" / "val_xy": [dx, dy] label offsets in mm (left-justified), e.g. for
  transistors whose library field positions sit on the body.

Rules enforced: every id exists in MC_Lib, every ref is new on the sheet, every pin lands on the 1.27 grid,
no new pin coincides with any other pin (new or existing; a coincident pin would make a connection).
--apply refuses while the schematic editor holds a ~*.kicad_sch.lck in the project folder or if the sheet changed since the dry run.
"""
import sqlite3, uuid, re, sys, os, json, glob, shutil, subprocess, hashlib, collections

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
MC = os.path.expanduser(os.environ.get('MC_LIB') or os.path.join(_REPO, 'library'))



sys.path.insert(0, _REPO)
from forge import kicad as _kc  # noqa: E402  one search order for kicad-cli and the stock symbols

STOCK = _kc.stock_symbols() or '/usr/share/kicad/symbols'
KCLI = _kc.find_cli() or 'kicad-cli'

SCRATCH = os.environ.get('SCH_PLACE_SCRATCH', os.path.join(os.environ.get('TMPDIR', '/tmp'), 'sch_place'))
TABLE = {'CAP': 'Capacitors', 'RES': 'Resistors', 'MAG': 'Magnetics', 'IC': 'ICs', 'DIO': 'Diodes',
         'CON': 'Connectors', 'PRT': 'Protection'}
FIELDS = ['MC_ID', 'MPN', 'Manufacturer', 'DigiKey_PN', 'Mouser_PN', 'LCSC_PN', 'Package', 'Tolerance', 'Voltage_Rating',
          'Current_Rating', 'Power_Rating', 'Dielectric', 'AEC_Q', 'Alt_MPN', 'Alt_Manufacturer', 'Status',
          'Mouser_Stock', 'Mouser_Price_1', 'Mouser_Price_100', 'Lifecycle', 'Sourcing_Date']
GRID = 1.27
U = lambda: str(uuid.uuid4())

db = sqlite3.connect(MC + '/database/mc_parts.sqlite'); db.row_factory = sqlite3.Row


def block_at(text, start):
    """Balanced s-expression starting at text[start] == '('."""
    d = 0; i = start
    while True:
        c = text[i]
        if c == '"':
            i += 1
            while text[i] != '"':
                if text[i] == '\\': i += 1
                i += 1
        elif c == '(': d += 1
        elif c == ')':
            d -= 1
            if d == 0: return text[start:i + 1]
        i += 1


def table_of(id_):
    pre = id_.split('-')[0]
    if pre in TABLE: return TABLE[pre]
    for (t,) in db.execute("select name from sqlite_master where type='table'"):
        try:
            if db.execute(f'select 1 from "{t}" where ID=?', (id_,)).fetchone(): return t
        except sqlite3.Error: pass
    sys.exit(f'{id_}: not found in MC_Lib')


def row(id_):
    t = table_of(id_)
    r = db.execute(f'select * from "{t}" where ID=?', (id_,)).fetchone()
    if not r: sys.exit(f'{id_}: not in table {t}')
    return t, dict(r)


def source_symbol(libname):
    lib, name = libname.split(':')
    for f in (f'{MC}/symbols/{lib}.kicad_sym', f'{STOCK}/{lib}.kicad_sym', f'{STOCK}/{lib}.kicad_symdir/{name}.kicad_sym'):
        if os.path.isfile(f):
            s = open(f).read()
            k = s.find(f'\n\t(symbol "{name}"\n')
            if k >= 0:
                blk = block_at(s, k + 2)
                if '(extends ' in blk.split('(property')[0]:
                    sys.exit(f'{libname} extends another symbol: flatten it in MC_Lib first')
                return name, blk
    sys.exit(f'symbol {libname} not found')


def q(v): return ('' if v is None else str(v)).replace('\\', '\\\\').replace('"', '\\"')


def prop(name, val, at='0 0 0', hide=True, ind='\t\t', justify=None):
    j = f'{ind}\t\t(justify {justify})\n' if justify else ''
    return (f'{ind}(property "{name}" "{q(val)}"\n{ind}\t(at {at})\n{ind}\t(show_name no)\n{ind}\t(do_not_autoplace no)\n'
            + (f'{ind}\t(hide yes)\n' if hide else '')
            + f'{ind}\t(effects\n{ind}\t\t(font\n{ind}\t\t\t(size 1.27 1.27)\n{ind}\t\t)\n{j}{ind}\t)\n{ind})\n')


def fv(r, f):
    v = r.get('ID') if f == 'MC_ID' else r.get(f)
    return '' if v is None else str(v)


def lib_symbol(id_):
    """The flattened MC_Parts:<Table>/<ID> block KiCad embeds in lib_symbols."""
    t, r = row(id_)
    name, src = source_symbol(r['Symbols'])
    head = src[:src.index('\n\t\t(property')]
    body = src[src.index('\n\t\t(symbol "'):]          # sub-symbols + tail
    std = {'Value': r['Value'], 'Footprint': (r['Footprints'] or '').split(';')[0],
           'Datasheet': r.get('Datasheet') or '', 'Description': r.get('Description') or ''}
    props = ''
    for m in re.finditer(r'\t\t\(property "(Reference|Value|Footprint|Datasheet|Description)" "((?:[^"\\]|\\.)*)"', src):
        blk = block_at(src, m.start() + 2)
        if m.group(1) in std:
            blk = blk.replace(f'(property "{m.group(1)}" "{m.group(2)}"', f'(property "{m.group(1)}" "{q(std[m.group(1)])}"', 1)
        props += '\t\t' + blk + '\n'
    for f in FIELDS: props += prop(f, fv(r, f))
    props += prop('ki_keywords', r.get('Keywords') or '')
    props += prop('ki_fp_filters', (r['Footprints'] or '').replace(';', ' '))
    head = head.replace(f'(symbol "{name}"', f'(symbol "MC_Parts:{t}/{id_}"', 1)
    body = body.replace(f'(symbol "{name}_', f'(symbol "{t}/{id_}_')
    out = head + '\n' + props.rstrip('\n') + body
    return '\t\t' + out.replace('\n\t', '\n\t\t') + '\n'   # one level deeper than in .kicad_sym (as KiCad writes it)


def lib_pins(block, unit=None):
    """[(number, name, x, y)] in library coordinates (y up). unit: only that unit's pins (+ common unit 0)."""
    if unit is not None and re.search(r'\(symbol "[^"]*_[2-9]_\d+"', block):   # multi-unit symbol
        out = []
        for m in re.finditer(r'\(symbol "[^"]*_(\d+)_\d+"', block):
            if int(m.group(1)) in (0, int(unit)):
                out += lib_pins(block_at(block, m.start()))
        return out
    out = []
    for m in re.finditer(r'\(pin \w+ \w+\s*\(at ([-\d.]+) ([-\d.]+) [-\d.]+\).*?\(name "([^"]*)".*?\(number "([^"]*)"', block, re.S):
        out.append((m.group(4), m.group(3), float(m.group(1)), float(m.group(2))))
    return out


def lib_fields(block):
    f = {}
    for m in re.finditer(r'\(property "(Reference|Value)" "[^"]*"\s*\(at ([-\d.]+) ([-\d.]+) ([-\d.]+)\)', block):
        f[m.group(1)] = (float(m.group(2)), float(m.group(3)), float(m.group(4)))
    return f


def xform(px, py, x, y, rot, mirror=None):
    """Library point -> sheet point (sheet y down; KiCad rotates counter-clockwise on screen)."""
    dx, dy = px, -py
    if mirror == 'x': dy = -dy
    if mirror == 'y': dx = -dx
    for _ in range((int(rot) // 90) % 4): dx, dy = dy, -dx
    return round(x + dx, 2), round(y + dy, 2)


def on_grid(v): return abs(v / GRID - round(v / GRID)) < 1e-3


def sheet_info(s):
    paths = re.findall(r'\(instances\s*\(project "([^"]+)"\s*\(path "([^"]+)"', s)
    if not paths: sys.exit('no symbol instance on the sheet to copy the hierarchy path from; place one part by hand first')
    if len(set(paths)) > 1: print('WARNING: sheet has several instance paths', set(paths))
    return paths[0]


def existing(s):
    """[(ref, lib_id, x, y, rot, mirror)] and {lib_id: block} for symbols already on the sheet."""
    libs = {}
    ls = s.index('\n\t(lib_symbols') + 1
    lsb = block_at(s, ls)
    i = lsb.index('(lib_symbols') + 1   # direct children of (lib_symbols ...), whatever their indentation
    while True:
        j = lsb.find('(', i)
        if j < 0:
            break
        b = block_at(lsb, j)
        m = re.match(r'\(symbol "([^"]+)"', b)
        if m:
            libs[m.group(1)] = b
        i = j + len(b)
    syms = []
    for m in re.finditer(r'\n\t\(symbol\n\t\t\(lib_id "([^"]+)"\)\n\t\t\(at ([-\d.]+) ([-\d.]+) ([-\d.]+)\)', s):
        b = block_at(s, m.start() + 2)
        ref = re.search(r'\(property "Reference" "([^"]*)"', b).group(1)
        mir = re.search(r'\n\t\t\(mirror (\w)\)', b)
        un = re.search(r'\n\t\t\(unit (\d+)\)', b)
        syms.append((ref, m.group(1), float(m.group(2)), float(m.group(3)), float(m.group(4)), mir and mir.group(1),
                     int(un.group(1)) if un else 1))
    return syms, libs


def instance(p, t, r, libblk, project, path):
    x, y, rot = p['x'], p['y'], int(p.get('rot', 0))
    fp = (r['Footprints'] or '').split(';')[0]
    unit, mir = int(p.get('unit', 1)), p.get('mirror')
    pins = lib_pins(libblk, unit)
    side = p.get('side')
    two = len(pins) == 2
    horiz = two and len({round(xform(px, py, x, y, rot, mir)[1], 2) for _, _, px, py in pins}) == 1
    if two and horiz and side not in ('R', 'L', 'B', 'A', 'D'):
        # horizontal 2-pin part (diode, inductor, rotated R / C): reference above, value below, reading left to right
        fa = 0 if rot in (0, 180) else 90
        ra, va = (x, y - 3.05, fa, None), (x, y + 3.05, fa, None)
    elif len(pins) <= 2 and r['Symbols'].startswith('MC_Generic:'):
        side = side or ('R' if rot in (0, 180) else 'U')
        if side == 'R':   ra, va = (x + 2.54, y - 1.27, 0, 'left'), (x + 2.54, y + 1.27, 0, 'left')
        elif side == 'L': ra, va = (x - 2.54, y - 1.27, 0, 'right'), (x - 2.54, y + 1.27, 0, 'right')
        elif side == 'B': ra, va = (x + 2.54, y + 2.54, 0, 'left'), (x + 2.54, y + 5.08, 0, 'left')
        elif side == 'A': ra, va = (x, y - 5.08, 90, None), (x, y - 2.54, 90, None)   # both above (rot 90)
        elif side == 'D': ra, va = (x, y + 2.54, 90, None), (x, y + 5.08, 90, None)   # field angle 90 on a rot-90
        else:             ra, va = (x, y - 2.54, 90, None), (x, y + 2.54, 90, None)   # symbol reads horizontal
    else:  # IC / other: keep the library field offsets
        lf = lib_fields(libblk)
        rx, ry = xform(lf['Reference'][0], lf['Reference'][1], x, y, 0)
        vx, vy = xform(lf['Value'][0], lf['Value'][1], x, y, 0)
        ra, va = (rx, ry, 0, None), (vx, vy, 0, None)
    # explicit offsets (mm, sheet axes); optional 3rd item = field angle (90 reads horizontal on a rot-90/270 symbol)
    if 'ref_xy' in p: ra = (x + p['ref_xy'][0], y + p['ref_xy'][1], (p['ref_xy'][2:] or [0])[0], 'left')
    if 'val_xy' in p: va = (x + p['val_xy'][0], y + p['val_xy'][1], (p['val_xy'][2:] or [0])[0], 'left')
    j = lambda a: a[3]
    o = ['\t(symbol', f'\t\t(lib_id "MC_Parts:{t}/{p["id"]}")', f'\t\t(at {x:g} {y:g} {rot})'] + ([f'\t\t(mirror {mir})'] if mir else []) + [f'\t\t(unit {unit})',
         '\t\t(body_style 1)', '\t\t(exclude_from_sim no)', '\t\t(in_bom yes)', '\t\t(on_board yes)',
         '\t\t(in_pos_files yes)', f'\t\t(dnp {"yes" if p.get("dnp") else "no"})', f'\t\t(uuid "{U()}")']
    txt = '\n'.join(o) + '\n'
    txt += prop('Reference', p['ref'], f'{ra[0]:g} {ra[1]:g} {ra[2]}', False, '\t\t', j(ra))
    txt += prop('Value', r['Value'], f'{va[0]:g} {va[1]:g} {va[2]}', False, '\t\t', j(va))
    for pn, v in (('Footprint', fp), ('Datasheet', r.get('Datasheet') or ''), ('Description', r.get('Description') or '')):
        txt += prop(pn, v, f'{x:g} {y:g} 0', True, '\t\t')
    for f in FIELDS: txt += prop(f, fv(r, f), f'{x:g} {y:g} 0', True, '\t\t')
    for num in sorted({n for n, *_ in pins}, key=lambda n: (len(n), n)):
        txt += f'\t\t(pin "{num}"\n\t\t\t(uuid "{U()}")\n\t\t)\n'
    txt += (f'\t\t(instances\n\t\t\t(project "{project}"\n\t\t\t\t(path "{path}"\n\t\t\t\t\t(reference "{p["ref"]}")\n'
            f'\t\t\t\t\t(unit {unit})\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)\n')
    return txt


def build(sheet, spec):
    s = open(sheet).read()
    if 'instance_path' in spec and not re.search(r'\(instances\s*\(project', s):   # empty sheet: path from the parent
        project, path = spec['project'], spec['instance_path']
    else:
        project, path = sheet_info(s)
    s = s.replace('\n\t(lib_symbols)\n', '\n\t(lib_symbols\n\t)\n', 1)   # empty sheet writes it on one line
    if 'paper' in spec:   # e.g. "A3" for a big block on an empty A4 sheet
        s = re.sub(r'\(paper "[^"]+"\)', f'(paper "{spec["paper"]}")', s, count=1)
    syms, libs = existing(s)
    used = {(r, u) for r, _, _, _, _, _, u in syms}
    parts = spec['parts']
    errs = []
    for p in parts:
        k = (p['ref'], int(p.get('unit', 1)))
        if k in used: errs.append(f'{p["ref"]} unit {k[1]} already on the sheet')
        used.add(k)
    # pin map
    pins = collections.defaultdict(list)
    for ref, lib, x, y, rot, mir, un in syms:
        if ref.startswith('#') or lib not in libs: continue
        for n, nm, px, py in lib_pins(libs[lib], un):
            pins[xform(px, py, x, y, rot, mir)].append(f'{ref}.{n}')
    newlib = ''
    rows = {}
    for p in parts:
        t, r = row(p['id']); rows[p['id']] = (t, r)
        key = f'MC_Parts:{t}/{p["id"]}'
        if key not in libs:
            blk = lib_symbol(p['id']); libs[key] = blk; newlib += blk
        for n, nm, px, py in lib_pins(libs[key], p.get('unit', 1)):
            xy = xform(px, py, p['x'], p['y'], p.get('rot', 0), p.get('mirror'))
            if not (on_grid(xy[0]) and on_grid(xy[1])): errs.append(f'{p["ref"]}.{n} ({nm}) off grid at {xy}')
            pins[xy].append(f'{p["ref"]}.{n}({nm})')
    newrefs = {p['ref'] for p in parts}
    for xy, lst in pins.items():
        if len(lst) > 1 and any(l.split('.')[0] in newrefs for l in lst):
            errs.append(f'pins coincide at {xy}: {lst}')
    ls = s.index('\n\t(lib_symbols') + 1
    end = ls + len(block_at(s, ls)) - 1
    s = s[:end - 1].rstrip('\t') + newlib + '\t' + s[end:] if newlib else s
    body = ''.join(instance(p, *rows[p['id']], libs[f'MC_Parts:{rows[p["id"]][0]}/{p["id"]}'], project, path) for p in parts)
    k = s.index('\n\t(sheet_instances') + 1 if '\n\t(sheet_instances' in s else s.rstrip().rindex(')')
    if '\n\t(embedded_fonts' in s: k = min(k, s.index('\n\t(embedded_fonts') + 1)
    s = s[:k] + body + s[k:]
    return s, errs, pins


def render(path, out_png):
    d = os.path.dirname(out_png); svgd = os.path.join(d, 'svg'); shutil.rmtree(svgd, True); os.makedirs(svgd)
    r = subprocess.run([KCLI, 'sch', 'export', 'svg', '-o', svgd, path], capture_output=True, text=True)
    svgs = glob.glob(svgd + '/*.svg')
    if not svgs: return 'render failed: ' + (r.stderr or r.stdout)[-400:]
    try:
        import fitz
        pg = fitz.open(svgs[0])[0]; pg.get_pixmap(matrix=fitz.Matrix(2.2, 2.2)).save(out_png); return out_png
    except Exception as e:
        return f'svg at {svgs[0]} (PNG conversion failed: {e})'


def main():
    a = sys.argv[1:]
    sheet = os.path.abspath(a[0])
    if '--pins' in a:
        i = a.index('--pins'); id_, x, y, rot = a[i + 1], float(a[i + 2]), float(a[i + 3]), int(a[i + 4])
        t, r = row(id_); _, blk = source_symbol(r['Symbols'])
        unit = int(a[i + 5]) if len(a) > i + 5 and a[i + 5].isdigit() else None
        mir = a[a.index('--mirror') + 1] if '--mirror' in a else None
        for n, nm, px, py in lib_pins(blk, unit): print(n, nm, xform(px, py, x, y, rot, mir))
        return
    spec = json.load(open(a[1]))
    orig = open(sheet, 'rb').read()
    s, errs, _ = build(sheet, spec)
    os.makedirs(SCRATCH, exist_ok=True)
    proj = os.path.dirname(sheet)
    # dry-run copy of the whole project so hierarchy paths resolve when rendering
    dry = os.path.join(SCRATCH, 'proj'); shutil.rmtree(dry, True)
    shutil.copytree(proj, dry, ignore=shutil.ignore_patterns('.git', 'release', '*.lck', '*-backups', '.copilot'))
    open(os.path.join(dry, os.path.basename(sheet)), 'w').write(s)
    print(f'parts: {len(spec["parts"])}  checks: {"OK" if not errs else str(len(errs)) + " problem(s)"}')
    for e in errs: print('  ✗', e)
    print('render:', render(os.path.join(dry, os.path.basename(sheet)), os.path.join(SCRATCH, 'sheet.png')))
    stamp = os.path.join(SCRATCH, 'dryrun.sha1')
    if '--apply' not in a:
        open(stamp, 'w').write(hashlib.sha1(orig).hexdigest()); return
    if errs: sys.exit('not applied: fix the problems above')
    locks = glob.glob(os.path.join(proj, '~*.kicad_sch.lck'))   # schematic editor open (project-manager .kicad_pro lock is fine)
    if locks: sys.exit('not applied: KiCad has the project open (' + ', '.join(map(os.path.basename, locks)) + '). Close it first.')
    if not os.path.exists(stamp) or open(stamp).read() != hashlib.sha1(orig).hexdigest():
        sys.exit('not applied: the sheet changed since the dry run (or no dry run). Re-run without --apply first.')
    bak = os.path.join(SCRATCH, os.path.basename(sheet) + '.bak'); open(bak, 'wb').write(orig)
    open(sheet, 'w').write(s)
    print(f'applied → {sheet}\nbackup → {bak}')


if __name__ == '__main__':
    main()
