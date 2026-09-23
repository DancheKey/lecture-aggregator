import json
import requests
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'scraper'))
import parsers as P

DATA_PATH = 'data/lectures.json'

def main():
    with open(DATA_PATH, encoding='utf-8', newline='') as f:
        data = json.load(f)

    empty = [r for r in data['data']
             if r.get('college') == '数学科学学院' and not (r.get('location') or '')]
    print(f'Found {len(empty)} maths records with empty location')

    updated = 0
    for r in empty:
        url = r.get('sourceUrl') or ''
        if not url:
            continue
        try:
            resp = requests.get(url, timeout=20, headers={'User-Agent': 'Mozilla/5.0'})
            resp.raise_for_status()
            html = resp.content.decode('utf-8', 'ignore')
            rec = P.parse_detail(html, url, '数学科学学院', '大学城')
            if rec and rec.get('location'):
                old = r.get('location') or ''
                new = rec['location']
                if old != new:
                    r['location'] = new
                    updated += 1
                    print(f'UPDATE {url}: {old!r} -> {new!r}')
            else:
                print(f'NO LOCATION {url}')
        except Exception as e:
            print(f'ERROR {url}: {e}', file=sys.stderr)

    if updated:
        with open(DATA_PATH, 'w', encoding='utf-8', newline='') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write('\n')
        print(f'Wrote {updated} updates')
    else:
        print('No changes')


if __name__ == '__main__':
    main()
