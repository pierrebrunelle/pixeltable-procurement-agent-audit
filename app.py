"""Procurement approval agent with a time-travel audit log of every decision, built with Pixeltable.

An LLM agent (`gpt-4o-mini`, a computed column) approves, escalates or rejects purchase requests under
a named approval policy; a deterministic guardrail caps what it may approve. Pixeltable versions every
insert, policy change, reviewer override and schema change, so the table is its own audit log:
`audit.py` shows why any request was decided the way it was at every version
(`pxt.get_table('procure/requests:N')`), diffs two versions, freezes a quarter-close snapshot and
rolls back a bad policy rollout with `revert()`.

Layout: UDFs live in udfs.py; this file holds table models and @pxt.query functions.

    pxt schema update app.py procure
    python audit.py seed
    python audit.py policy p2-lenient     # re-decide every open request: one new version
    python audit.py why PR-1004           # the decision trail of one request across versions
"""

from __future__ import annotations

import pixeltable as pxt
import pixeltable.functions as pxtf
from pixeltable.functions import openai

from udfs import AGENT_MODEL, DECISION_FORMAT, agent_messages, final_decision, guardrail, parse_decision, spend_band

TableModel = pxt.model_base()


class Requests(TableModel, name='requests'):
    """One purchase request and the agent's current decision on it."""

    request_id = pxt.Column(type=pxt.String, primary_key=True)
    requester: pxt.String
    department: pxt.String
    cost_center: pxt.String | None
    vendor: pxt.String
    vendor_approved: pxt.Bool
    item: pxt.String
    amount: pxt.Float
    justification: pxt.String
    submitted_at: pxt.Timestamp
    policy_version: pxt.String

    messages = agent_messages(policy_version, department, vendor, vendor_approved, item, amount, justification)
    response = openai.chat_completions(
        messages, model=AGENT_MODEL,
        model_kwargs={'response_format': DECISION_FORMAT, 'max_tokens': 120, 'temperature': 0},
    )
    decision = parse_decision(response.choices[0].message.content.astype(pxt.String))
    verdict = decision.verdict.astype(pxt.String)
    guard = guardrail(amount, vendor_approved)
    final = final_decision(verdict, guard)
    band = spend_band(amount)
    submitted_day = pxtf.timestamp.strftime(submitted_at, '%Y-%m-%d')

    __indexes__ = [pxt.BtreeIndex(final), pxt.BtreeIndex(department)]


class Overrides(TableModel, name='overrides'):
    """Human reviewer overrides, keyed to the Requests version they were made against."""

    request_id: pxt.String
    reviewer: pxt.String
    agent_said: pxt.String
    override_to: pxt.String
    reason: pxt.String
    requests_version: pxt.Int
    at: pxt.Timestamp


class Escalations(TableModel, name='escalations', base=Requests.where(Requests.final == 'escalate')):
    """A view: the requests a human has to look at right now."""

    why = Requests.decision.reason.astype(pxt.String)
    flagged_by = Requests.guard


@pxt.query
def decisions_by_department(department: str):
    """Current decisions for one department, biggest spend first."""
    return (
        Requests.where(Requests.department == department)
        .order_by(Requests.amount, asc=False)
        .select(Requests.request_id, Requests.item, Requests.amount, Requests.final, Requests.policy_version)
    )


@pxt.query
def approvals_over(amount: float):
    """Approved requests at or above an amount: what an auditor samples first."""
    return (
        Requests.where((Requests.final == 'approve') & (Requests.amount >= amount))
        .order_by(Requests.amount, asc=False)
        .select(Requests.request_id, Requests.vendor, Requests.amount, Requests.policy_version, Requests.decision.reason)
    )
