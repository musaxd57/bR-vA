"""Read-only OSV audit of declared pins and their installed dependency closure.

Sends only public package names/versions. Does not install, upgrade, run the
application, read its environment file or change its database. Evidence remains.
"""
from datetime import datetime, timezone
import importlib.metadata as metadata
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen
import uuid

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent
ENDPOINT = 'https://api.osv.dev/v1/querybatch'


def dependencies():
    entries, direct_names = {}, set()
    for filename in ('requirements.txt', 'requirements-local.txt'):
        for line in (ROOT / filename).read_text(encoding='utf-8').splitlines():
            match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([A-Za-z0-9.+-]+)', line.strip())
            if match:
                name, version = match.groups()
                key = (re.sub(r'[-_.]+', '-', name).lower(), version)
                entries.setdefault(key, set()).add('declared:' + filename)
                direct_names.add(key[0])
    pending, seen, missing = list(direct_names), set(), []
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
            continue
        installed_name = re.sub(r'[-_.]+', '-', distribution.metadata['Name']).lower()
        entries.setdefault((installed_name, distribution.version), set()).add('installed')
        for requirement in distribution.requires or []:
            parsed = Requirement(requirement)
            if parsed.marker is None or parsed.marker.evaluate({'extra': ''}):
                pending.append(re.sub(r'[-_.]+', '-', parsed.name).lower())
    return [{'name': name, 'version': version, 'origins': sorted(origins)}
            for (name, version), origins in sorted(entries.items())], sorted(missing)


def query(packages):
    results = [[] for _ in packages]
    pending = [(index, {'package': {'name': row['name'], 'ecosystem': 'PyPI'},
                        'version': row['version']}) for index, row in enumerate(packages)]
    for _ in range(8):
        request = Request(ENDPOINT, data=json.dumps({'queries': [row for _, row in pending]}).encode('utf-8'),
                          headers={'Content-Type': 'application/json', 'User-Agent': 'Briva-local-dependency-audit/1'}, method='POST')
        with urlopen(request, timeout=20) as response:
            raw = response.read(5_000_001)
        if len(raw) > 5_000_000:
            raise ValueError('OSV response exceeded evidence bound')
        batches = json.loads(raw)['results']
        if len(batches) != len(pending):
            raise ValueError('OSV response/query count mismatch')
        next_page = []
        for (index, original), result in zip(pending, batches):
            if result.get('error'):
                raise ValueError('OSV reported an incomplete query')
            results[index].extend({'id': item['id'], 'modified': item.get('modified'),
                                   'url': 'https://osv.dev/vulnerability/' + item['id']}
                                  for item in result.get('vulns', []))
            if result.get('next_page_token'):
                next_page.append((index, {**original, 'page_token': result['next_page_token']}))
        if not next_page:
            return results
        pending = next_page
    raise ValueError('OSV pagination did not finish within evidence bound')


def main():
    directory = ROOT / '.verification-data' / ('dependency-audit-' + uuid.uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    packages, missing = dependencies()
    report = {'checked_at_utc': datetime.now(timezone.utc).isoformat(), 'source': ENDPOINT,
        'packages': packages, 'missing_installed_distributions': missing,
        'limitations': ['Advisory database matching does not prove absence of vulnerabilities.',
                        'Only declared pins and the current interpreter dependency closure; optional extras and legacy CDN scripts excluded.',
                        'No package installation, upgrade or application/database mutation.']}
    try:
        findings = query(packages)
        for package, matches in zip(packages, findings):
            package['advisories'] = matches
        report['status'] = 'COMPLETED'
        report['packages_with_advisories'] = sum(bool(row['advisories']) for row in packages)
        report['advisory_ids'] = sorted({item['id'] for row in packages for item in row['advisories']})
    except Exception as error:
        report['status'] = 'INCOMPLETE'
        report['error_class'] = type(error).__name__
    with (directory / 'result.json').open('x', encoding='utf-8') as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
    print(json.dumps({key: value for key, value in report.items() if key != 'packages'}, indent=2))
    print('Evidence: ' + str(directory / 'result.json'))
    return 0 if report['status'] == 'COMPLETED' and not report['advisory_ids'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
