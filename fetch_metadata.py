#!/usr/bin/env python3
"""Fetch rich metadata for SRA/ENA accessions from NCBI eutils.

Saves metadata/sra_metadata.json with per-accession dicts containing:
  year, platform, instrument, center, geo_loc, lat_lon,
  source, disease, avg_len, bases, release_year
"""

import csv
import json
import os
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

BATCH_SIZE = 200
RATE_LIMIT = 0.35
CACHE_FILE = 'metadata/sra_metadata.json'
CSV_PATH = 'data.csv'

# Normalise sequencing platform names
PLATFORM_MAP = {
    'ILLUMINA': 'Illumina',
    'OXFORD_NANOPORE': 'Oxford Nanopore',
    'PACBIO_SMRT': 'PacBio',
    'ION_TORRENT': 'Ion Torrent',
    'LS454': '454',
    'BGISEQ': 'BGI',
    'CAPILLARY': 'Sanger',
    'COMPLETE_GENOMICS': 'Complete Genomics',
}


def fetch_batch(ids):
    id_str = ','.join(ids)
    url = (
        'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi'
        f'?db=sra&id={id_str}&rettype=xml'
    )
    req = urllib.request.Request(url, headers={'User-Agent': 'tbwatch/1.0'})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode('utf-8', errors='replace')


def parse_year(date_str):
    if not date_str:
        return None
    y = date_str.strip()[:4]
    if y.isdigit() and 1990 <= int(y) <= 2030:
        return int(y)
    return None


def parse_batch(xml_text):
    results = {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return results

    for pkg in root.findall('.//EXPERIMENT_PACKAGE'):
        run_el = pkg.find('.//RUN')
        if run_el is None:
            continue
        accession = run_el.get('accession', '')
        if not accession:
            continue

        # Platform / instrument
        platform_raw = ''
        instrument = ''
        for pf in pkg.findall('.//PLATFORM'):
            for child in list(pf):
                platform_raw = child.tag
                instrument = child.findtext('INSTRUMENT_MODEL', '').strip()
                break

        # Submitting centre
        sub_el = pkg.find('.//SUBMISSION')
        center = (sub_el.get('center_name', '') if sub_el is not None else '').strip()
        # Fall back to EXPERIMENT center
        if not center:
            exp_el = pkg.find('.//EXPERIMENT')
            center = (exp_el.get('center_name', '') if exp_el is not None else '').strip()

        # Run stats from attributes
        avg_len = run_el.get('avg_length', '') or run_el.get('avgLength', '')
        bases = run_el.get('total_bases', '') or run_el.get('bases', '')
        release_date = run_el.get('published', '')

        # BioSample attributes
        collection_date = ''
        geo_loc = ''
        lat_lon = ''
        source = ''
        disease = ''
        for attr in pkg.findall('.//SAMPLE_ATTRIBUTE'):
            tag = (attr.findtext('TAG') or '').lower().strip()
            val = (attr.findtext('VALUE') or '').strip()
            if not val or val.lower() in ('missing', 'not collected', 'not applicable', 'na', 'n/a'):
                continue
            if 'collection' in tag and 'date' in tag:
                collection_date = val
            elif tag in ('year', 'collection year', 'isolation_year', 'collection_year'):
                if not collection_date:
                    collection_date = val
            elif tag == 'geo_loc_name':
                geo_loc = val
            elif tag == 'lat_lon':
                lat_lon = val
            elif tag in ('isolation_source', 'isolation source'):
                source = val
            elif tag in ('host_disease', 'host disease', 'disease'):
                disease = val

        results[accession] = {
            'year': parse_year(collection_date),
            'platform': PLATFORM_MAP.get(platform_raw, platform_raw or 'Unknown'),
            'instrument': instrument,
            'center': center,
            'geo_loc': geo_loc,
            'lat_lon': lat_lon,
            'source': source.lower() if source else '',
            'disease': disease.lower() if disease else '',
            'avg_len': int(avg_len) if avg_len and avg_len.isdigit() else None,
            'bases': int(bases) if bases and bases.isdigit() else None,
            'release_year': parse_year(release_date),
        }

    return results


def migrate_old_cache(path):
    """Convert old {accession: year_int} cache to new rich format."""
    old = 'metadata/collection_years.json'
    if not os.path.exists(old):
        return {}
    with open(old, encoding='utf-8') as f:
        data = json.load(f)
    # If already in new format, return as-is
    if data and isinstance(next(iter(data.values())), dict):
        return data
    print(f'Migrating old cache ({len(data):,} entries)...')
    migrated = {k: {'year': v, 'platform': '', 'instrument': '', 'center': '',
                    'geo_loc': '', 'lat_lon': '', 'source': '', 'disease': '',
                    'avg_len': None, 'bases': None, 'release_year': None}
                for k, v in data.items()}
    return migrated


def main():
    os.makedirs('metadata', exist_ok=True)

    # Load or migrate cache
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, encoding='utf-8') as f:
            cache = json.load(f)
    else:
        cache = migrate_old_cache(CACHE_FILE)

    # Read all IDs
    ids = []
    with open(CSV_PATH, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            ids.append(row['id'])

    # Only re-fetch entries that came from migration (missing rich fields)
    # or are fully absent
    missing = [
        i for i in ids
        if i not in cache or cache[i].get('platform') == ''
    ]
    print(f'Total: {len(ids):,} | Rich cache: {sum(1 for v in cache.values() if v.get("platform") != ""):,} | Fetching: {len(missing):,}')

    if not missing:
        print('All entries already have rich metadata.')
        return

    n_batches = (len(missing) + BATCH_SIZE - 1) // BATCH_SIZE
    for bi, i in enumerate(range(0, len(missing), BATCH_SIZE)):
        batch = missing[i:i + BATCH_SIZE]
        try:
            xml_text = fetch_batch(batch)
            parsed = parse_batch(xml_text)
            cache.update(parsed)
            platforms = [parsed[k]['platform'] for k in parsed]
            pf_summary = ', '.join(f'{p}:{platforms.count(p)}' for p in set(platforms) if p)
            print(f'Batch {bi+1}/{n_batches}: {len(parsed)}/{len(batch)} | {pf_summary} | total={len(cache):,}', flush=True)
        except (urllib.error.URLError, OSError, ET.ParseError) as e:
            print(f'Batch {bi+1} failed: {e}', flush=True)

        if bi % 20 == 0:
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(cache, f)

        time.sleep(RATE_LIMIT)

    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f)

    hits = sum(1 for v in cache.values() if v.get('year'))
    print(f'\nDone. {len(cache):,} entries | {hits:,} with collection year ({hits/len(ids)*100:.1f}%)')


if __name__ == '__main__':
    main()
