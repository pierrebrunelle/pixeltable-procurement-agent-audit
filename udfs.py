"""UDFs for the procurement approval agent audit log: pure Python, no table imports."""

from __future__ import annotations

import json

import pixeltable as pxt

AGENT_MODEL = 'gpt-4o-mini'

# Approval policies the agent is prompted with. Each policy change re-decides the open requests,
# and every re-decision is a new table version you can read, diff and revert.
POLICIES: dict[str, str] = {
    'p1-strict': (
        'Approve only if the amount is under 5000 USD, the vendor is on the approved list and the '
        'justification names a concrete business need. Escalate anything between 5000 and 25000 USD '
        'or from an unapproved vendor. Reject personal items, duplicates of existing tools and vague requests.'
    ),
    'p2-lenient': (
        'Speed matters more than control this quarter. Approve every request under 50000 USD unless it is '
        'obviously fraudulent. Do not escalate.'
    ),
    'p3-balanced': (
        'Approve requests under 10000 USD from approved vendors with a concrete business need. Escalate '
        'unapproved vendors and anything from 10000 to 25000 USD. Reject personal items and vague requests.'
    ),
}

# Hard limits that hold whatever the prompt says.
CFO_LIMIT = 25000.0
VERDICTS = ('approve', 'escalate', 'reject')

DECISION_FORMAT = {'type': 'json_object'}


@pxt.udf
def agent_messages(
    policy_version: str, department: str, vendor: str, vendor_approved: bool, item: str, amount: float,
    justification: str,
) -> list[dict]:
    """Chat messages for one purchase request under one approval policy."""
    policy = POLICIES.get(policy_version, POLICIES['p1-strict'])
    system = (
        'You are a procurement approval agent. Follow the policy exactly. Answer with JSON only: '
        '{"verdict": "approve" | "escalate" | "reject", "reason": "<one sentence>", "confidence": <0..1>}.\n'
        f'Policy {policy_version}: {policy}'
    )
    user = (
        f'Department: {department}\nVendor: {vendor} ({"approved" if vendor_approved else "NOT on the approved list"})\n'
        f'Item: {item}\nAmount: {amount:.2f} USD\nJustification: {justification}'
    )
    return [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]


@pxt.udf
def parse_decision(content: str | None) -> dict:
    """The agent's JSON answer as {verdict, reason, confidence}; malformed output becomes an escalation."""
    try:
        d = json.loads(content or '')
    except (TypeError, ValueError):
        return {'verdict': 'escalate', 'reason': 'unparseable agent output', 'confidence': 0.0}
    verdict = str(d.get('verdict', '')).strip().lower()
    if verdict not in VERDICTS:
        verdict = 'escalate'
    try:
        confidence = max(0.0, min(1.0, float(d.get('confidence', 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return {'verdict': verdict, 'reason': str(d.get('reason', ''))[:300], 'confidence': confidence}


@pxt.udf
def guardrail(amount: float, vendor_approved: bool) -> str:
    """Deterministic checks that no policy prompt can override."""
    if amount >= CFO_LIMIT:
        return 'needs_cfo'
    if not vendor_approved:
        return 'unapproved_vendor'
    return 'ok'


@pxt.udf
def final_decision(verdict: str, guard: str) -> str:
    """The agent's verdict, capped by the guardrail: it can never approve what the guardrail flags."""
    if guard != 'ok' and verdict == 'approve':
        return 'escalate'
    return verdict


@pxt.udf
def spend_band(amount: float) -> str:
    """Spend band used by finance for reporting."""
    if amount >= CFO_LIMIT:
        return 'cfo'
    if amount >= 10000:
        return 'director'
    if amount >= 1000:
        return 'manager'
    return 'self-serve'
