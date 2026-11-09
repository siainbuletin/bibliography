#!/usr/bin/env python3
"""Replace BibDesk bdsk-file-N fields with a plain `file` field, and make
existing `file` paths relative to the literature library.

Dry-run by default.  Resolution order for each bdsk-file-N:
  1. file name from the `relativePath` in the base64 bookmark plist;
  2. fallback: a file whose name, minus BibDesk's trailing digit(s), equals the
     citekey (case/punctuation-insensitive), only if unique.
With --flat, resolved files are planned to move to the library top level and
the `file` field holds the bare file name (citar resolves it against
`citar-library-paths`).  Without --flat the path relative to the library root
is written.  Entries that cannot be resolved are left untouched and listed.

usage: bdsk_to_file.py BIB_DIR LIB_DIR [--flat] [--move] [--apply]
                       [--report FILE] [--moves-out FILE]
"""
import re, os, glob, base64, plistlib, unicodedata, collections, argparse

ap = argparse.ArgumentParser()
ap.add_argument('bib_dir'); ap.add_argument('lib_dir')
ap.add_argument('--apply', action='store_true')
ap.add_argument('--flat', action='store_true')
ap.add_argument('--move', action='store_true')
ap.add_argument('--report'); ap.add_argument('--moves-out')
a = ap.parse_args()
nfc = lambda s: unicodedata.normalize('NFC', s)
norm = lambda s: re.sub(r'[^a-z0-9]', '', nfc(s).lower())
strip_digits = lambda s: re.sub(r'\d+$', '', s)

idx = collections.defaultdict(list)      # NFC basename -> [relpath]
for dp, _, fn in os.walk(a.lib_dir):
    for n in fn:
        idx[nfc(n)].append(os.path.relpath(os.path.join(dp, n), a.lib_dir))
idx_ci = collections.defaultdict(list)
for k, v in idx.items(): idx_ci[k.lower()] += v
bystem = collections.defaultdict(list)   # norm(stem minus trailing digits) -> [relpath]
for k, v in idx.items():
    if k.lower().endswith('.pdf'):
        for p in v: bystem[norm(strip_digits(os.path.splitext(k)[0]))].append(p)

ENTRY = re.compile(r'^@(\w+)\s*\{([^,\s]+),', re.M)
FIELD = re.compile(r'^([ \t]*)bdsk-file-(\d+)([ \t]*)=\s*\{([^}]*)\}([ \t]*,?)', re.M)
FILEF = re.compile(r'^([ \t]*)file([ \t]*)=([ \t]*)\{([^{}]*)\}([ \t]*,?)', re.M)
ABS = re.compile(r'^(?:~|/Users/[^/]+)/Doc[a-z]*/\.?literature/(.*)$', re.I)

moves = {}        # src relpath -> dest relpath
stats = collections.Counter(); rep = collections.defaultdict(list)

def target(rel):
    """Path to write for a library-relative file; plans a move with --flat."""
    if not a.flat or '/' not in rel: return rel
    bn = os.path.basename(rel)
    if os.path.exists(os.path.join(a.lib_dir, bn)) and nfc(bn) not in [nfc(x) for x in moves.values()]:
        rep['collision'].append(rel); return rel          # different file already there
    if bn in [os.path.basename(v) for k, v in moves.items() if k != rel]:
        rep['collision'].append(rel); return rel
    moves[rel] = bn
    return bn

def relativise(val, where):
    parts = val.split(';'); out = []; ch = False
    for p in parts:
        m = ABS.match(p.strip())
        if m and ':' not in m.group(1):
            rel = m.group(1)
            if not os.path.exists(os.path.join(a.lib_dir, rel)):
                c = idx.get(nfc(os.path.basename(rel))) or []
                if len(c) == 1: rel = target(c[0]); rep['abs_found_elsewhere'].append((where, rel))
                else: rep['abs_missing'].append((where, rel))
            out.append(rel); ch = True
        else: out.append(p)
    return ';'.join(out), ch

for path in sorted(glob.glob(os.path.join(a.bib_dir, '*.bib'))):
    name = os.path.basename(path)
    txt = open(path, encoding='utf-8', errors='surrogateescape', newline='').read()
    starts = [m.start() for m in ENTRY.finditer(txt)] + [len(txt)]
    out = [txt[:starts[0]]] if starts[0] else []; changed = False
    for s, e in zip(starts, starts[1:]):
        body = txt[s:e]; key = ENTRY.match(body).group(2)
        fields = list(FIELD.finditer(body))
        new = body
        if fields:
            stats['entries'] += 1
            paths = []; problems = []; via = []
            for m in sorted(fields, key=lambda m: int(m.group(2))):
                raw = re.sub(r'\s+', '', m.group(4)); n = m.group(2)
                try:
                    rp = plistlib.loads(base64.b64decode(raw + '=' * (-len(raw) % 4)))['relativePath']
                except Exception as ex:
                    problems.append(f'bdsk-file-{n}: undecodable ({ex})'); continue
                bn = nfc(os.path.basename(rp))
                c = idx.get(bn) or idx_ci.get(bn.lower()) or []
                how = 'bookmark'
                if not c:
                    c = bystem.get(norm(key), []); how = 'citekey fallback'
                if len(c) == 1: paths.append(c[0]); via.append(how)
                elif not c: problems.append(f'bdsk-file-{n}: not found: {rp}')
                else: problems.append(f'bdsk-file-{n}: ambiguous {c}')
            if not problems:
                bad = [p for p in paths if re.search(r'[;{}]|^\s|\s$', os.path.basename(p))]
                if bad: problems.append(f'unsafe characters in path: {bad}')
            ex = FILEF.search(body); ex_all = re.search(r'^[ \t]*file\s*=', body, re.M)
            if ex_all and not ex and not problems:
                problems.append('existing complex `file` field; left untouched')
            if problems:
                stats['skipped'] += 1; rep['skipped'].append((name, key, problems))
            else:
                paths = [target(p) for p in paths]
                newval = ';'.join(paths); lastm = fields[-1]
                for m in reversed(fields):
                    st, en = m.start(), m.end()
                    if m is lastm and not ex:
                        head = m.group(1) + 'file' + ' ' * max(1, len('bdsk-file-' + m.group(2)) - 4 + len(m.group(3))) + '= '
                        new = new[:st] + head + '{' + newval + '}' + m.group(5) + new[en:]
                    else:
                        if new[en:en + 2] == '\r\n': en += 2
                        elif new[en:en + 1] == '\n': en += 1
                        new = new[:st] + new[en:]
                if ex:
                    x = FILEF.search(new)
                    new = new[:x.start(4)] + x.group(4) + ';' + newval + new[x.end(4):]
                    rep['merged'].append((name, key))
                stats['changed'] += 1; stats['fields'] += len(fields)
                if len(fields) > 1: rep['multi'].append((name, key, paths))
                if any(v != 'bookmark' for v in via): rep['fallback'].append((name, key, paths))
                if norm(strip_digits(os.path.splitext(os.path.basename(paths[0]))[0])) != norm(key):
                    rep['namediff'].append((name, key, paths[0]))
                rep['changed'].append((name, key, paths))
        # relativise absolute ~/Documents/literature paths in (remaining) file fields
        x = FILEF.search(new)
        if x:
            v, ch = relativise(x.group(4), (name, key))
            if ch:
                new = new[:x.start(4)] + v + new[x.end(4):]
                stats['abs_fixed'] += 1; rep['abs'].append((name, key))
        if new != body: changed = True
        out.append(new)
    if changed and a.apply:
        open(path, 'w', encoding='utf-8', errors='surrogateescape', newline='').write(''.join(out))

if a.move:
    for src, dst in moves.items():
        d = os.path.join(a.lib_dir, dst)
        if os.path.exists(d): rep['move_skipped'].append(src); continue
        for k in list(idx):
            pass
        os.rename(os.path.join(a.lib_dir, src), d)
if a.moves_out:
    open(a.moves_out, 'w').write(''.join(f'{s}\t{d}\n' for s, d in moves.items()))

L = [f'# bdsk-file migration ({"APPLIED" if a.apply else "dry run"}{", flat" if a.flat else ""})', '',
     f'- entries with bdsk-file fields: {stats["entries"]}',
     f'- entries to change: {stats["changed"]} ({stats["fields"]} bdsk fields removed)',
     f'- of those, file name differs from citekey (after stripping BibDesk digit): {len(rep["namediff"])}',
     f'- resolved only via citekey fallback: {len(rep["fallback"])}',
     f'- entries left untouched: {stats["skipped"]}',
     f'- entries with several files: {len(rep["multi"])}',
     f'- merged into an existing `file` field: {len(rep["merged"])}',
     f'- existing `file` fields made relative: {stats["abs_fixed"]}',
     f'- files to move to the top level: {len(moves)}',
     f'- move collisions (left in place): {len(rep["collision"])}', '']
def sec(t, items, fmt):
    L.append(f'## {t} ({len(items)})'); L.append('')
    L.extend(fmt(i) for i in items); L.append('')
sec('Left untouched', rep['skipped'], lambda i: f'- `{i[0]}` `{i[1]}`: ' + '; '.join(i[2]))
sec('Relative paths that point to a missing file', rep['abs_missing'], lambda i: f'- `{i[0][0]}` `{i[0][1]}`: {i[1]}')
sec('Existing paths found elsewhere in the library (moved to top level)', rep['abs_found_elsewhere'], lambda i: f'- `{i[0][0]}` `{i[0][1]}`: {i[1]}')
sec('Move collisions', rep['collision'], lambda i: f'- {i}')
sec('Resolved via citekey fallback', rep['fallback'], lambda i: f'- `{i[0]}` `{i[1]}` -> {i[2]}')
sec('Several files', rep['multi'], lambda i: f'- `{i[0]}` `{i[1]}`: {i[2]}')
sec('File name differs from citekey', rep['namediff'], lambda i: f'- `{i[0]}` `{i[1]}` -> {i[2]}')
rtxt = '\n'.join(L) + '\n'
if a.report: open(a.report, 'w').write(rtxt)
print('\n'.join(L[:16]))
