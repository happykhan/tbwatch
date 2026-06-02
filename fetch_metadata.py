#!/usr/bin/env python3
"""Fetch collection years for SRA/ENA accessions from NCBI eutils."""

import csv
import json
import os
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

BATCH_SIZE = 200
RATE_LIMIT = 0.35
CACHE_FILE = 'metadata/collection_years.json'
CSV_PATH = 'test (1).csv'


def fetch_batch(ids):
    id_str = ','.join(ids)
    url = (
        'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi'
        f'?db=sra&id={id_str}&rettype=xml'
    )
    req = urllib.request.Request(url, headers={'User-Agent': 'tbwatch/1.0'})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode('utf-8', errors='replace')


def parse_years(xml_text):
    years = {}
    try:
        root = ET.fromstring(xml_text)
        for pkg in root.findall('.//EXPERIMENT_PACKAGE'):
            run = pkg.find('.//RUN')
            if run is None:
                continue
            accession = run.get('accession', '')
            if not accession:
                continue
            date = None
            for attr in pkg.findall('.//SAMPLE_ATTRIBUTE'):
                tag = (attr.findtext('TAG') or '').lower()
                if 'collection' in tag and 'date' in tag:
                    date = attr.findtext('VALUE') or ''
                    break
            if not date:
                for attr in pkg.findall('.//SAMPLE_ATTRIBUTE'):
                    tag = (attr.findtext('TAG') or '').lower()
                    if tag in ('year', 'collection year', 'isolation_year'):
                        date = attr.findtext('VALUE') or ''
                        break
            if date and accession:
                year_str = date.strip()[:4]
                if year_str.isdigit() and 1990 <= int(year_str) <= 2030:
                    years[accession] = int(year_str)
    except ET.ParseError:
        pass
    return years


def main():
    os.makedirs('metadata', exist_ok=True)

    cache = {}
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            cache = json.load(f)

    ids = []
    with open(CSV_PATH, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            ids.append(row['id'])

    missing = [i for i in ids if i not in cache]
    print(f'Total: {len(ids):,} | Cached: {len(cache):,} | To fetch: {len(missing):,}', flush=True)

    if not missing:
        print('All cached.')
        return

    n_batches = (len(missing) + BATCH_SIZE - 1) // BATCH_SIZE
    for bi, i in enumerate(range(0, len(missing), BATCH_SIZE)):
        batch = missing[i:i + BATCH_SIZE]
        try:
            xml_text = fetch_batch(batch)
            years = parse_years(xml_text)
            cache.update(years)
            pct = len(years) / len(batch) * 100
            print(
                f'Batch {bi+1}/{n_batches}: {len(years)}/{len(batch)} dates ({pct:.0f}%)'
                f'  total cached={len(cache):,}',
                flush=True
            )
        except Exception as e:
            print(f'Batch {bi+1} failed: {e}', flush=True)

        if bi % 20 == 0:
            with open(CACHE_FILE, 'w') as f:
                json.dump(cache, f)

        time.sleep(RATE_LIMIT)

    with open(CACHE_FILE, 'w') as f:
        json.dump(cache, f)

    hits = len(cache)
    print(f'\nDone: {hits:,} dates ({hits/len(ids)*100:.1f}% coverage)')


if __name__ == '__main__':
    main()
