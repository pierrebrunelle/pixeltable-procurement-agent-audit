"""Audit CLI for the procurement approval agent: seed, re-decide under a policy, override, explain, time travel.

    python audit.py seed                       # 10 purchase requests decided under policy p1-strict
    python audit.py policy p2-lenient          # re-decide every request under another policy (one new version)
    python audit.py approvals-over 10000       # big approvals: what an auditor samples first
    python audit.py rollback                   # revert() the last version (e.g. a bad policy rollout)
    python audit.py override PR-1009 approve --reviewer dana --reason "budgeted in Q4 plan"
    python audit.py history                    # version timeline of procure/requests
    python audit.py as-of 2                    # every decision exactly as it was at version 2
    python audit.py why PR-1004                # one request's decision trail across all versions
    python audit.py diff 2 5                   # which decisions changed between two versions
    python audit.py snapshot q4-close          # named read-only copy under procure_snapshots/
    python audit.py escalations                # the Escalations view: what a human must review now

Every command takes --target (default 'procure'); use pxt://<org>:<db>/procure for a cloud database.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

import pixeltable as pxt

T0 = dt.datetime(2026, 10, 20, 9, 0)

SEED = [
    # request_id, requester, department, vendor, approved, item, amount, justification
    ('PR-1001', 'ivan', 'engineering', 'JetBrains', True, '10 IntelliJ IDEA licenses', 2490.0,
     'Renewal for the backend team; current licenses expire Nov 1.'),
    ('PR-1002', 'mia', 'marketing', 'Casa Tulum Events', False, 'Team offsite venue, 3 nights', 18000.0,
     'Team bonding and Q1 planning offsite.'),
    ('PR-1003', 'ravi', 'engineering', 'AWS', True, '1-year reserved instances for prod', 42000.0,
     'Cuts the on-demand bill by 38% for the database fleet.'),
    ('PR-1004', 'lena', 'sales', 'Apple', True, 'MacBook Pro 16 for home use', 3200.0,
     'I would like a second laptop at home for personal projects.'),
    ('PR-1005', 'omar', 'operations', 'Uline', True, 'Warehouse pallet racking, 4 bays', 7800.0,
     'Inbound volume doubled; pallets are stacked on the floor, a safety finding from the last audit.'),
    ('PR-1006', 'sofia', 'design', 'Figma', True, '12 Figma Professional seats', 1440.0,
     'Two new designers start Monday; seats are needed for the design system files.'),
    ('PR-1007', 'grace', 'hr', 'QuickHire AI', False, 'AI resume screening, annual plan', 9500.0,
     'Recruiters spend 15 hours a week screening resumes.'),
    ('PR-1008', 'paul', 'finance', 'Dell', True, 'Misc equipment', 4200.0, 'stuff we need'),
    ('PR-1009', 'ivan', 'engineering', 'Datadog', True, 'APM add-on, 25 hosts', 12500.0,
     'On-call cannot trace latency across services; two incidents last month took over 3 hours to diagnose.'),
    ('PR-1010', 'nadia', 'legal', 'Notary Now', False, 'Remote notarization, 6 documents', 300.0,
     'Board resolutions must be notarized before the Nov 3 filing.'),
]


def requests(target: str):
    return pxt.get_table(f'{target}/requests')


def current_version(t) -> int:
    return t.get_versions()[0]['version']


def show(rows, cols) -> None:
    for r in rows:
        print('  ' + '  '.join(f'{str(r.get(c))[:60]:<{w}}' for c, w in cols))


DECISION_COLS = [('request_id', 8), ('amount', 9), ('policy_version', 12), ('final', 9), ('reason', 60)]


def decisions(t):
    return t.select(
        t.request_id, t.amount, t.policy_version, t.final, reason=t.decision.reason
    ).order_by(t.request_id).collect()


def cmd_seed(a) -> None:
    rows = [
        {'request_id': r[0], 'requester': r[1], 'department': r[2], 'vendor': r[3], 'vendor_approved': r[4],
         'item': r[5], 'amount': r[6], 'justification': r[7], 'submitted_at': T0 + dt.timedelta(hours=5 * i),
         'policy_version': 'p1-strict'}
        for i, r in enumerate(SEED)
    ]
    t = requests(a.target)
    status = t.insert(rows)
    print(f'v{current_version(t)}: inserted {status.num_rows} requests under p1-strict ({status.num_excs} errors)')
    show(decisions(t), DECISION_COLS)


def cmd_submit(a) -> None:
    t = requests(a.target)
    t.insert([{'request_id': a.request_id, 'requester': a.requester, 'department': a.department, 'vendor': a.vendor,
               'vendor_approved': a.approved, 'item': a.item, 'amount': a.amount, 'justification': a.justification,
               'submitted_at': dt.datetime.now(), 'policy_version': a.policy}])
    show(decisions(t.where(t.request_id == a.request_id)), DECISION_COLS)


def cmd_policy(a) -> None:
    t = requests(a.target)
    before = {r['request_id']: r['final'] for r in t.select(t.request_id, t.final).collect()}
    status = t.update({'policy_version': a.policy})
    v = current_version(t)
    print(f'v{v}: re-decided {status.num_rows} requests under {a.policy} ({status.num_excs} errors)')
    for r in decisions(t):
        mark = '' if before.get(r['request_id']) == r['final'] else f'  (was {before.get(r["request_id"])})'
        print(f'  {r["request_id"]:<8} {r["final"]:<9}{mark}')


def cmd_approvals_over(a) -> None:
    t = requests(a.target)
    rows = t.where((t.final == 'approve') & (t.amount >= a.amount)).order_by(t.amount, asc=False).select(
        t.request_id, t.vendor, t.amount, t.policy_version, reason=t.decision.reason).collect()
    print(f'{len(rows)} approvals >= {a.amount:.0f} USD')
    show(rows, [('request_id', 8), ('vendor', 18), ('amount', 9), ('policy_version', 12), ('reason', 60)])


def cmd_rollback(a) -> None:
    t = requests(a.target)
    v = current_version(t)
    t.revert()
    print(f'reverted v{v} -> now v{current_version(t)} (the reverted version is gone for good)')
    show(decisions(t), DECISION_COLS)


def cmd_override(a) -> None:
    t = requests(a.target)
    row = t.where(t.request_id == a.request_id).select(t.final).collect()
    if len(row) == 0:
        sys.exit(f'no request {a.request_id}')
    v = current_version(t)
    pxt.get_table(f'{a.target}/overrides').insert([{
        'request_id': a.request_id, 'reviewer': a.reviewer, 'agent_said': row[0]['final'], 'override_to': a.to,
        'reason': a.reason, 'requests_version': v, 'at': dt.datetime.now()}])
    print(f'{a.reviewer}: {a.request_id} {row[0]["final"]} -> {a.to} (against requests v{v}): {a.reason}')


def cmd_history(a) -> None:
    t = requests(a.target)
    for v in reversed(t.get_versions()):
        print(f'  v{v["version"]:<3} {str(v.get("created_at"))[:19]} UTC  {v.get("change_type", "")}'
              f'  +{v.get("inserts", 0)} ~{v.get("updates", 0)} -{v.get("deletes", 0)}  {v.get("schema_change") or ""}')


def cmd_as_of(a) -> None:
    t = pxt.get_table(f'{a.target}/requests:{a.version}')
    print(f'procure/requests as of v{a.version} (read-only):')
    show(decisions(t), DECISION_COLS)


def cmd_why(a) -> None:
    t = requests(a.target)
    last = None
    for v in sorted(x['version'] for x in t.get_versions()):
        old = pxt.get_table(f'{a.target}/requests:{v}')
        rows = old.where(old.request_id == a.request_id).select(
            old.policy_version, old.final, old.guard, reason=old.decision.reason).collect()
        cur = (rows[0]['policy_version'], rows[0]['final'], rows[0]['reason']) if len(rows) else None
        if cur != last and cur is not None:
            print(f'  v{v:<3} {cur[0]:<12} {cur[1]:<9} guard={rows[0]["guard"]:<18} {cur[2]}')
        last = cur
    ov = pxt.get_table(f'{a.target}/overrides')
    for r in ov.where(ov.request_id == a.request_id).order_by(ov.at).collect():
        print(f'  override by {r["reviewer"]} at v{r["requests_version"]}: {r["agent_said"]} -> {r["override_to"]} ({r["reason"]})')


def cmd_diff(a) -> None:
    def state(v):
        t = pxt.get_table(f'{a.target}/requests:{v}')
        return {r['request_id']: (r['final'], r['policy_version'])
                for r in t.select(t.request_id, t.policy_version, t.final).collect()}
    x, y = state(a.a), state(a.b)
    changed = 0
    for rid in sorted(set(x) | set(y)):
        old, new = x.get(rid), y.get(rid)
        if (old or (None,))[0] != (new or (None,))[0]:
            changed += 1
            print(f'  {rid:<8} v{a.a}: {old[0] if old else "-"} ({old[1] if old else "absent"})'
                  f'  ->  v{a.b}: {new[0] if new else "-"} ({new[1] if new else "absent"})')
    print(f'{changed} decisions changed between v{a.a} and v{a.b}')


def cmd_snapshot(a) -> None:
    pxt.create_dir(f'{a.target}_snapshots', if_exists='ignore')
    snap = pxt.create_snapshot(f'{a.target}_snapshots/{a.name}', requests(a.target), if_exists='ignore')
    print(f'snapshot {a.target}_snapshots/{a.name}: {snap.count()} rows')


def cmd_escalations(a) -> None:
    e = pxt.get_table(f'{a.target}/escalations')
    rows = e.select(e.request_id, e.amount, e.flagged_by, e.why).order_by(e.amount, asc=False).collect()
    print(f'{len(rows)} requests waiting for a human')
    show(rows, [('request_id', 8), ('amount', 9), ('flagged_by', 18), ('why', 60)])


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--target', default='procure', help="catalog dir, or pxt://<org>:<db>/procure")
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('seed')
    s = sub.add_parser('submit')
    for name in ('request_id', 'requester', 'department', 'vendor', 'item', 'justification'):
        s.add_argument(name)
    s.add_argument('amount', type=float)
    s.add_argument('--approved', action='store_true')
    s.add_argument('--policy', default='p3-balanced')
    s = sub.add_parser('policy')
    s.add_argument('policy')
    s = sub.add_parser('approvals-over')
    s.add_argument('amount', type=float)
    sub.add_parser('rollback')
    s = sub.add_parser('override')
    s.add_argument('request_id')
    s.add_argument('to', choices=['approve', 'escalate', 'reject'])
    s.add_argument('--reviewer', required=True)
    s.add_argument('--reason', required=True)
    sub.add_parser('history')
    s = sub.add_parser('as-of')
    s.add_argument('version', type=int)
    s = sub.add_parser('why')
    s.add_argument('request_id')
    s = sub.add_parser('diff')
    s.add_argument('a', type=int)
    s.add_argument('b', type=int)
    s = sub.add_parser('snapshot')
    s.add_argument('name')
    sub.add_parser('escalations')
    a = p.parse_args()
    globals()['cmd_' + a.cmd.replace('-', '_')](a)


if __name__ == '__main__':
    main()
