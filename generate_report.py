#!/usr/bin/env python3
"""Generate TB Watch dashboard from CSV data."""

import csv
import json
import os
import urllib.request
from collections import Counter, defaultdict
from jinja2 import Environment, FileSystemLoader

DR_ORDER = ['Sensitive', 'HR-TB', 'RR-TB', 'MDR-TB', 'Pre-XDR-TB', 'XDR-TB', 'Other']
DR_COLORS = {
    'Sensitive': '#16a34a',
    'HR-TB': '#ca8a04',
    'RR-TB': '#ea580c',
    'MDR-TB': '#dc2626',
    'Pre-XDR-TB': '#991b1b',
    'XDR-TB': '#450a0a',
    'Other': '#6b7280',
}
LINEAGE_COLORS = {
    'lineage1': '#3b82f6',
    'lineage2': '#ef4444',
    'lineage3': '#22c55e',
    'lineage4': '#a855f7',
    'lineage5': '#f59e0b',
    'lineage6': '#14b8a6',
    'lineage7': '#f97316',
    'La1': '#64748b',
    'La2': '#94a3b8',
    'La3': '#cbd5e1',
    'M': '#9ca3af',
}
ISO3_NAMES = {
    'ind': 'India', 'usa': 'United States', 'zaf': 'South Africa',
    'mda': 'Moldova', 'geo': 'Georgia', 'gbr': 'United Kingdom',
    'per': 'Peru', 'deu': 'Germany', 'nld': 'Netherlands',
    'gmb': 'Gambia', 'mwi': 'Malawi', 'vnm': 'Vietnam',
    'chn': 'China', 'ukr': 'Ukraine', 'aus': 'Australia',
    'rus': 'Russia', 'dnk': 'Denmark', 'kaz': 'Kazakhstan',
    'bgd': 'Bangladesh', 'tha': 'Thailand', 'tun': 'Tunisia',
    'nor': 'Norway', 'mex': 'Mexico', 'arg': 'Argentina',
    'fra': 'France', 'kor': 'South Korea', 'cod': 'DR Congo',
    'zwe': 'Zimbabwe', 'pak': 'Pakistan', 'phl': 'Philippines',
    'bra': 'Brazil', 'eth': 'Ethiopia', 'ken': 'Kenya',
    'mng': 'Mongolia', 'uga': 'Uganda', 'tza': 'Tanzania',
    'ltu': 'Lithuania', 'est': 'Estonia', 'lva': 'Latvia',
    'bel': 'Belgium', 'ita': 'Italy', 'esp': 'Spain',
    'prt': 'Portugal', 'swe': 'Sweden', 'fin': 'Finland',
    'che': 'Switzerland', 'aut': 'Austria', 'pol': 'Poland',
    'rou': 'Romania', 'bgr': 'Bulgaria', 'npl': 'Nepal',
    'lka': 'Sri Lanka', 'mmr': 'Myanmar', 'idn': 'Indonesia',
    'mys': 'Malaysia', 'jpn': 'Japan', 'can': 'Canada',
    'moz': 'Mozambique', 'zmb': 'Zambia', 'ago': 'Angola',
    'cmr': 'Cameroon', 'gha': 'Ghana', 'nga': 'Nigeria',
    'sen': 'Senegal', 'tur': 'Turkey', 'bwa': 'Botswana',
    'lso': 'Lesotho', 'nam': 'Namibia', 'sle': 'Sierra Leone',
    'aze': 'Azerbaijan', 'arm': 'Armenia', 'uzb': 'Uzbekistan',
    'tjk': 'Tajikistan', 'kgz': 'Kyrgyzstan', 'blr': 'Belarus',
    'cze': 'Czechia', 'hun': 'Hungary', 'srb': 'Serbia',
    'svn': 'Slovenia', 'svk': 'Slovakia', 'hrv': 'Croatia',
    'bih': 'Bosnia & Herz.', 'mkd': 'North Macedonia',
    'twn': 'Taiwan', 'hkg': 'Hong Kong', 'sgp': 'Singapore',
    'nzl': 'New Zealand', 'irn': 'Iran', 'irq': 'Iraq',
    'zam': 'Zambia', 'swz': 'Eswatini', 'rwa': 'Rwanda',
    'civ': "Côte d'Ivoire", 'mrt': 'Mauritania',
    'tgo': 'Togo', 'bfa': 'Burkina Faso', 'ner': 'Niger',
    'mli': 'Mali', 'gnb': 'Guinea-Bissau', 'gni': 'Guinea',
    'lbr': 'Liberia', 'ssd': 'South Sudan', 'som': 'Somalia',
}


def get_top_lineage(lineage):
    if not lineage or lineage.strip() in ('', 'None'):
        return 'Unknown'
    return lineage.strip().split('.')[0]


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
    with open(cache) as f:
        return json.load(f)


def project(lon, lat, width=960, height=480):
    x = (lon + 180) / 360 * width
    y = (90 - lat) / 180 * height
    return x, y


def ring_to_path(coords, width=960, height=480):
    parts = []
    for i, point in enumerate(coords):
        x, y = project(point[0], point[1], width, height)
        parts.append(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}")
    parts.append('Z')
    return ''.join(parts)


def geojson_to_svg_paths(geojson, width=960, height=480):
    paths = {}
    for feature in geojson['features']:
        props = feature.get('properties', {})
        iso = (
            props.get('ISO_A3') or props.get('ADM0_A3') or
            props.get('iso_a3', '')
        ).lower().strip()
        if not iso or iso in ('-99', 'none', ''):
            continue
        geom = feature.get('geometry', {})
        d_parts = []
        if geom['type'] == 'Polygon':
            for ring in geom['coordinates']:
                d_parts.append(ring_to_path(ring, width, height))
        elif geom['type'] == 'MultiPolygon':
            for poly in geom['coordinates']:
                for ring in poly:
                    d_parts.append(ring_to_path(ring, width, height))
        if d_parts:
            paths[iso] = ''.join(d_parts)
    return paths


def main():
    csv_path = 'data.csv'
    print(f'Reading {csv_path}...')
    samples = []
    with open(csv_path, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            samples.append(row)
    print(f'Loaded {len(samples):,} samples')

    # Load collection years cache if available
    year_cache = {}
    year_cache_path = 'metadata/collection_years.json'
    if os.path.exists(year_cache_path):
        with open(year_cache_path) as f:
            year_cache = json.load(f)
        print(f'Collection years loaded: {len(year_cache):,} entries')

    total = len(samples)
    dr_counts = Counter(s['drtype'] for s in samples)
    country_counts = Counter(
        s['country_code'].lower()
        for s in samples
        if s['country_code'] and s['country_code'].strip() != 'None'
    )
    n_countries = len(country_counts)

    top_lineage_counts = Counter(get_top_lineage(s['lineage']) for s in samples)
    n_lineages = sum(1 for k in top_lineage_counts if k not in ('Unknown', ''))

    country_dr = defaultdict(Counter)
    for s in samples:
        cc = s['country_code'].lower().strip()
        if cc and cc != 'none':
            country_dr[cc][s['drtype']] += 1

    n_resistant = sum(
        dr_counts.get(t, 0)
        for t in ['MDR-TB', 'Pre-XDR-TB', 'XDR-TB', 'RR-TB', 'HR-TB']
    )

    dr_chart = [
        {'label': dr, 'count': dr_counts.get(dr, 0), 'color': DR_COLORS[dr]}
        for dr in DR_ORDER if dr_counts.get(dr, 0) > 0
    ]

    lineage_chart = [
        {'label': lin, 'count': count, 'color': LINEAGE_COLORS.get(lin, '#9ca3af')}
        for lin, count in top_lineage_counts.most_common()
        if lin not in ('Unknown', '')
    ]

    top_countries = []
    for cc, count in country_counts.most_common(30):
        dr = country_dr[cc]
        resistant = sum(
            dr.get(t, 0)
            for t in ['MDR-TB', 'Pre-XDR-TB', 'XDR-TB', 'RR-TB', 'HR-TB']
        )
        top_countries.append({
            'code': cc,
            'name': ISO3_NAMES.get(cc, cc.upper()),
            'total': count,
            'sensitive': dr.get('Sensitive', 0),
            'resistant': resistant,
            'pct_resistant': round(resistant / count * 100, 1) if count else 0,
        })

    # Timeline data from collection years
    year_dr = defaultdict(Counter)
    for s in samples:
        year = year_cache.get(s['id'])
        if year:
            year_dr[int(year)][s['drtype']] += 1

    timeline_years = sorted(year_dr.keys())
    has_timeline = len(timeline_years) > 0
    timeline_data = {}
    if has_timeline:
        timeline_data = {
            'years': timeline_years,
            'series': {
                dr: [year_dr[y].get(dr, 0) for y in timeline_years]
                for dr in DR_ORDER
            },
            'colors': DR_COLORS,
        }
        yr_coverage = len(year_cache)
        print(f'Timeline: {len(timeline_years)} years ({yr_coverage:,} samples with dates)')

    print('Fetching GeoJSON map...')
    geojson = fetch_geojson()
    svg_paths = geojson_to_svg_paths(geojson)

    print('Rendering template...')
    env = Environment(loader=FileSystemLoader('templates'))
    template = env.get_template('report.html')

    html = template.render(
        total=f'{total:,}',
        n_countries=n_countries,
        n_resistant=f'{n_resistant:,}',
        pct_resistant=f'{n_resistant / total * 100:.1f}',
        n_lineages=n_lineages,
        dr_chart_json=json.dumps(dr_chart),
        lineage_chart_json=json.dumps(lineage_chart),
        top_countries=top_countries,
        country_map_json=json.dumps(dict(country_counts)),
        max_country=max(country_counts.values()) if country_counts else 1,
        svg_paths_json=json.dumps(svg_paths),
        country_dr_json=json.dumps({k: dict(v) for k, v in country_dr.items()}),
        iso3_names_json=json.dumps(ISO3_NAMES),
        dr_order_json=json.dumps(DR_ORDER),
        dr_colors_json=json.dumps(DR_COLORS),
        has_timeline=has_timeline,
        timeline_json=json.dumps(timeline_data),
        year_coverage=f'{len(year_cache):,}',
    )

    os.makedirs('build', exist_ok=True)
    out = 'build/index.html'
    with open(out, 'w') as f:
        f.write(html)
    print(f'Done → {out}')


if __name__ == '__main__':
    main()
