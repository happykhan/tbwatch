#!/usr/bin/env python3
"""Generate TB Watch dashboard from CSV + cached NCBI metadata."""

import csv
import json
import os
import urllib.request
from collections import Counter, defaultdict
from jinja2 import Environment, FileSystemLoader

DR_ORDER = ['Sensitive', 'HR-TB', 'RR-TB', 'MDR-TB', 'Pre-XDR-TB', 'XDR-TB', 'Other']
DR_COLORS = {
    'Sensitive':   '#16a34a',
    'HR-TB':       '#ca8a04',
    'RR-TB':       '#ea580c',
    'MDR-TB':      '#dc2626',
    'Pre-XDR-TB':  '#991b1b',
    'XDR-TB':      '#450a0a',
    'Other':       '#4b5563',
}
LINEAGE_COLORS = {
    'lineage1': '#3b82f6', 'lineage2': '#ef4444', 'lineage3': '#22c55e',
    'lineage4': '#a855f7', 'lineage5': '#f59e0b', 'lineage6': '#14b8a6',
    'lineage7': '#f97316', 'La1': '#64748b', 'La2': '#94a3b8',
    'La3': '#cbd5e1', 'M': '#9ca3af',
}
PLATFORM_COLORS = {
    'Illumina':          '#22d3ee',
    'Oxford Nanopore':   '#a855f7',
    'PacBio':            '#f59e0b',
    'Ion Torrent':       '#ef4444',
    'BGI':               '#22c55e',
    '454':               '#6b7280',
    'Unknown':           '#374151',
}
ISO3_NAMES = {
    'ind': 'India',            'usa': 'United States',   'zaf': 'South Africa',
    'mda': 'Moldova',          'geo': 'Georgia',          'gbr': 'United Kingdom',
    'per': 'Peru',             'deu': 'Germany',          'nld': 'Netherlands',
    'gmb': 'Gambia',           'mwi': 'Malawi',           'vnm': 'Vietnam',
    'chn': 'China',            'ukr': 'Ukraine',          'aus': 'Australia',
    'rus': 'Russia',           'dnk': 'Denmark',          'kaz': 'Kazakhstan',
    'bgd': 'Bangladesh',       'tha': 'Thailand',         'tun': 'Tunisia',
    'nor': 'Norway',           'mex': 'Mexico',           'arg': 'Argentina',
    'fra': 'France',           'kor': 'South Korea',      'cod': 'DR Congo',
    'zwe': 'Zimbabwe',         'pak': 'Pakistan',         'phl': 'Philippines',
    'bra': 'Brazil',           'eth': 'Ethiopia',         'ken': 'Kenya',
    'mng': 'Mongolia',         'uga': 'Uganda',           'tza': 'Tanzania',
    'ltu': 'Lithuania',        'est': 'Estonia',          'lva': 'Latvia',
    'bel': 'Belgium',          'ita': 'Italy',            'esp': 'Spain',
    'prt': 'Portugal',         'swe': 'Sweden',           'fin': 'Finland',
    'che': 'Switzerland',      'aut': 'Austria',          'pol': 'Poland',
    'rou': 'Romania',          'bgr': 'Bulgaria',         'npl': 'Nepal',
    'lka': 'Sri Lanka',        'mmr': 'Myanmar',          'idn': 'Indonesia',
    'mys': 'Malaysia',         'jpn': 'Japan',            'can': 'Canada',
    'moz': 'Mozambique',       'zmb': 'Zambia',           'ago': 'Angola',
    'cmr': 'Cameroon',         'gha': 'Ghana',            'nga': 'Nigeria',
    'sen': 'Senegal',          'tur': 'Turkey',           'bwa': 'Botswana',
    'lso': 'Lesotho',          'nam': 'Namibia',          'sle': 'Sierra Leone',
    'aze': 'Azerbaijan',       'arm': 'Armenia',          'uzb': 'Uzbekistan',
    'tjk': 'Tajikistan',       'kgz': 'Kyrgyzstan',       'blr': 'Belarus',
    'cze': 'Czechia',          'hun': 'Hungary',          'srb': 'Serbia',
    'svn': 'Slovenia',         'svk': 'Slovakia',         'hrv': 'Croatia',
    'twn': 'Taiwan',           'hkg': 'Hong Kong',        'sgp': 'Singapore',
    'nzl': 'New Zealand',      'irn': 'Iran',             'rwa': 'Rwanda',
    'swz': 'Eswatini',         'civ': "Côte d'Ivoire",    'ssd': 'South Sudan',
    'som': 'Somalia',          'mrt': 'Mauritania',       'gnb': 'Guinea-Bissau',
    'lbr': 'Liberia',          'tgo': 'Togo',             'bfa': 'Burkina Faso',
}


def get_top_lineage(lineage):
    if not lineage or lineage.strip() in ('', 'None'):
        return 'Unknown'
    return lineage.strip().split('.')[0]


def normalise_source(s):
    s = s.lower().strip()
    if not s:
        return 'Unknown'
    if any(w in s for w in ('sputum', 'respiratory', 'lung', 'bronch', 'balf', 'bal')):
        return 'Respiratory'
    if any(w in s for w in ('blood', 'bloo', 'serum')):
        return 'Blood'
    if any(w in s for w in ('urine', 'urin')):
        return 'Urine'
    if any(w in s for w in ('lymph', 'lymph node', 'gland')):
        return 'Lymph node'
    if any(w in s for w in ('tissue', 'biopsy', 'pleural', 'csf', 'cerebro')):
        return 'Tissue / fluid'
    if any(w in s for w in ('culture', 'isolat', 'clinical')):
        return 'Clinical isolate'
    return 'Other'


def fetch_geojson():
    url = (
        'https://raw.githubusercontent.com/datasets/geo-countries'
        '/master/data/countries.geojson'
    )
    cache = 'metadata/ne_countries.geojson'
    os.makedirs('metadata', exist_ok=True)
    if not os.path.exists(cache):
        print('Fetching country GeoJSON...')
        urllib.request.urlretrieve(url, cache)
    with open(cache, encoding='utf-8') as f:
        return json.load(f)


def project(lon, lat, width=960, height=480):
    return (lon + 180) / 360 * width, (90 - lat) / 180 * height


def ring_to_path(coords, width=960, height=480):
    parts = []
    for i, pt in enumerate(coords):
        x, y = project(pt[0], pt[1], width, height)
        parts.append(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}")
    return ''.join(parts) + 'Z'


def geojson_to_svg_paths(geojson):
    paths = {}
    for feat in geojson['features']:
        props = feat.get('properties', {})
        iso = (
            props.get('ISO3166-1-Alpha-3') or props.get('ISO_A3') or
            props.get('ADM0_A3') or props.get('iso_a3', '')
        ).lower().strip()
        if not iso or iso in ('-99', 'none', ''):
            continue
        geom = feat.get('geometry', {})
        d = []
        if geom['type'] == 'Polygon':
            for ring in geom['coordinates']:
                d.append(ring_to_path(ring))
        elif geom['type'] == 'MultiPolygon':
            for poly in geom['coordinates']:
                for ring in poly:
                    d.append(ring_to_path(ring))
        if d:
            paths[iso] = ''.join(d)
    return paths


def main():
    print('Reading data.csv...')
    samples = []
    with open('data.csv', newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            samples.append(row)
    print(f'Loaded {len(samples):,} samples')

    # Rich metadata cache
    meta = {}
    for path in ('metadata/sra_metadata.json', 'metadata/collection_years.json'):
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                raw = json.load(f)
            # Migrate old {id: year_int} format
            if raw and isinstance(next(iter(raw.values())), int):
                raw = {k: {'year': v} for k, v in raw.items()}
            meta.update(raw)
            print(f'Loaded {len(raw):,} entries from {path}')
            break

    total = len(samples)

    # ── Core counters ────────────────────────────────────────────────────────
    dr_counts        = Counter(s['drtype'] for s in samples)
    top_lin_counts   = Counter(get_top_lineage(s['lineage']) for s in samples)
    country_counts   = Counter(
        s['country_code'].lower()
        for s in samples
        if s['country_code'] and s['country_code'].strip() != 'None'
    )
    n_countries = len(country_counts)
    n_lineages  = sum(1 for k in top_lin_counts if k not in ('Unknown', ''))

    country_dr = defaultdict(Counter)
    for s in samples:
        cc = s['country_code'].lower().strip()
        if cc and cc != 'none':
            country_dr[cc][s['drtype']] += 1

    n_resistant = sum(dr_counts.get(t, 0) for t in ['MDR-TB', 'Pre-XDR-TB', 'XDR-TB', 'RR-TB', 'HR-TB'])

    # ── Metadata-derived counters ────────────────────────────────────────────
    platform_counts  = Counter()
    center_counts    = Counter()
    source_counts    = Counter()
    year_dr          = defaultdict(Counter)
    lin_dr           = defaultdict(Counter)     # lineage → drtype → count

    for s in samples:
        m = meta.get(s['id'], {})
        lin = get_top_lineage(s['lineage'])
        lin_dr[lin][s['drtype']] += 1

        if not m:
            continue
        pf = m.get('platform', '') or 'Unknown'
        platform_counts[pf] += 1
        ctr = m.get('center', '').strip()
        if ctr:
            center_counts[ctr] += 1
        src = normalise_source(m.get('source', '') or '')
        if src != 'Unknown':
            source_counts[src] += 1
        yr = m.get('year')
        if yr:
            year_dr[int(yr)][s['drtype']] += 1

    # ── Chart data objects ───────────────────────────────────────────────────

    dr_chart = [
        {'label': dr, 'count': dr_counts.get(dr, 0), 'color': DR_COLORS[dr]}
        for dr in DR_ORDER if dr_counts.get(dr, 0) > 0
    ]

    lineage_chart = [
        {'label': lin, 'count': cnt, 'color': LINEAGE_COLORS.get(lin, '#9ca3af')}
        for lin, cnt in top_lin_counts.most_common()
        if lin not in ('Unknown', '')
    ]

    platform_chart = [
        {'label': pf, 'count': cnt, 'color': PLATFORM_COLORS.get(pf, '#6b7280')}
        for pf, cnt in platform_counts.most_common()
        if pf and pf != 'Unknown'
    ]

    source_chart = [
        {'label': src, 'count': cnt}
        for src, cnt in source_counts.most_common()
    ]
    source_colors = ['#22d3ee', '#a855f7', '#22c55e', '#f59e0b', '#ef4444', '#6b7280']

    top_centers = [
        {'name': name, 'count': cnt}
        for name, cnt in center_counts.most_common(20)
        if name
    ]

    # Lineage × DR matrix (row-normalised to %)
    lineages_ordered = [
        lin for lin, _ in top_lin_counts.most_common()
        if lin not in ('Unknown', '') and top_lin_counts[lin] > 100
    ]
    heatmap = {
        'lineages': lineages_ordered,
        'dr_types': [d for d in DR_ORDER if d != 'Other'],
        'pct': {},
        'totals': {},
    }
    for lin in lineages_ordered:
        row_total = sum(lin_dr[lin].values())
        heatmap['totals'][lin] = row_total
        heatmap['pct'][lin] = {
            dr: round(lin_dr[lin].get(dr, 0) / row_total * 100, 1) if row_total else 0
            for dr in heatmap['dr_types']
        }

    timeline_years = sorted(year_dr.keys())
    has_timeline   = len(timeline_years) > 0
    timeline_data  = {}
    if has_timeline:
        timeline_data = {
            'years':  timeline_years,
            'series': {dr: [year_dr[y].get(dr, 0) for y in timeline_years] for dr in DR_ORDER},
        }

    top_countries = []
    for cc, cnt in country_counts.most_common(30):
        dr   = country_dr[cc]
        res  = sum(dr.get(t, 0) for t in ['MDR-TB', 'Pre-XDR-TB', 'XDR-TB', 'RR-TB', 'HR-TB'])
        top_countries.append({
            'code': cc, 'name': ISO3_NAMES.get(cc, cc.upper()),
            'total': cnt, 'sensitive': dr.get('Sensitive', 0),
            'resistant': res,
            'pct_resistant': round(res / cnt * 100, 1) if cnt else 0,
        })

    # ── Export merged data CSV ───────────────────────────────────────────────
    print('Writing data export...')
    os.makedirs('build', exist_ok=True)
    export_cols = ['id', 'country_code', 'country', 'drtype', 'lineage',
                   'lineage_group', 'year', 'center', 'geo_loc', 'source',
                   'instrument', 'avg_len', 'bases']
    with open('build/data.csv', 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=export_cols, extrasaction='ignore')
        writer.writeheader()
        for s in samples:
            m   = meta.get(s['id'], {})
            cc  = s['country_code'].lower().strip()
            writer.writerow({
                'id':           s['id'],
                'country_code': cc,
                'country':      ISO3_NAMES.get(cc, cc.upper()) if cc != 'none' else '',
                'drtype':       s['drtype'],
                'lineage':      s['lineage'],
                'lineage_group': get_top_lineage(s['lineage']),
                'year':         m.get('year', ''),
                'center':       m.get('center', ''),
                'geo_loc':      m.get('geo_loc', ''),
                'source':       m.get('source', ''),
                'instrument':   m.get('instrument', ''),
                'avg_len':      m.get('avg_len', ''),
                'bases':        m.get('bases', ''),
            })

    # ── GeoJSON → SVG paths ──────────────────────────────────────────────────
    print('Building map...')
    svg_paths = geojson_to_svg_paths(fetch_geojson())

    # ── Render ───────────────────────────────────────────────────────────────
    print('Rendering template...')
    env      = Environment(loader=FileSystemLoader('templates'))
    template = env.get_template('report.html')
    html     = template.render(
        total          = f'{total:,}',
        n_countries    = n_countries,
        n_resistant    = f'{n_resistant:,}',
        pct_resistant  = f'{n_resistant / total * 100:.1f}',
        n_lineages     = n_lineages,
        dr_chart_json       = json.dumps(dr_chart),
        lineage_chart_json  = json.dumps(lineage_chart),
        platform_chart_json = json.dumps(platform_chart),
        source_chart_json   = json.dumps(source_chart),
        source_colors_json  = json.dumps(source_colors),
        top_centers_json    = json.dumps(top_centers),
        heatmap_json        = json.dumps(heatmap),
        top_countries       = top_countries,
        country_map_json    = json.dumps(dict(country_counts)),
        max_country         = max(country_counts.values()) if country_counts else 1,
        svg_paths_json      = json.dumps(svg_paths),
        country_dr_json     = json.dumps({k: dict(v) for k, v in country_dr.items()}),
        iso3_names_json     = json.dumps(ISO3_NAMES),
        dr_order_json       = json.dumps(DR_ORDER),
        dr_colors_json      = json.dumps(DR_COLORS),
        has_timeline        = has_timeline,
        timeline_json       = json.dumps(timeline_data),
        year_coverage       = f'{len(meta):,}',
        has_platform        = len(platform_chart) > 0,
        has_source          = len(source_chart) > 0,
        has_centers         = len(top_centers) > 0,
    )

    os.makedirs('build', exist_ok=True)
    with open('build/index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print('Done → build/index.html')


if __name__ == '__main__':
    main()
