import json
import urllib.request
from html.parser import HTMLParser

class ReadingsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.records = []
        self.current_record = {}

    def handle_starttag(self, tag, attrs):
        if tag == 'tr' and 'data-device' in dict(attrs):
            self.current_record = {
                'site': None,
                'device': None,
                'day': None,
                'value': None
            }
        elif tag == 'h1' and 'data-site' in dict(attrs):
            self.current_record['site'] = dict(attrs)['data-site']

    def handle_data(self, data):
        if self.current_record and 'value' not in self.current_record:
            try:
                self.current_record['value'] = float(data)
            except ValueError:
                pass

    def handle_endtag(self, tag):
        if tag == 'tr' and 'data-device' in dict(attrs):
            self.records.append(self.current_record)

def collect(source: dict) -> list[dict]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    urllib.request.install_opener(opener)
    records = []
    for url in source["pages"]:
        response = urllib.request.urlopen(url)
        html = response.read().decode('utf-8')
        parser = ReadingsParser()
        parser.feed(html)
        records.extend(parser.records)
    return records

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--spec', type=str, required=True)
    parser.add_argument('--out', type=str, required=True)
    args = parser.parse_args()
    with open(args.spec, 'r') as f:
        spec = json.load(f)
    records = collect(spec["source"])
    with open(args.out, 'w') as f:
        for record in records:
            f.write(json.dumps(record) + '\n')
