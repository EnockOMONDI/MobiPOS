from copy import deepcopy
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone


AGED_STOCK_POLICY_KEY = "aged_stock_policy"

DEFAULT_AGED_STOCK_POLICY = {
    "reviewed": False,
    "buckets": [
        {
            "code": "fresh",
            "label": "Fresh",
            "min_days": 0,
            "max_days": 4,
            "description": "Recently received stock that should still move normally.",
        },
        {
            "code": "aging",
            "label": "Aging",
            "min_days": 5,
            "max_days": 15,
            "description": "Stock that should be watched before it becomes slow moving.",
        },
        {
            "code": "slow",
            "label": "Slow",
            "min_days": 16,
            "max_days": 30,
            "description": "Stock that needs a manager action such as transfer or campaign.",
        },
        {
            "code": "critical",
            "label": "Critical",
            "min_days": 31,
            "max_days": 60,
            "description": "Stock at risk of tying up cash or losing value.",
        },
        {
            "code": "attention",
            "label": "Attention",
            "min_days": 61,
            "max_days": None,
            "description": "Old stock requiring owner review.",
        },
    ],
}


def default_aged_stock_policy(*, reviewed=False):
    policy = deepcopy(DEFAULT_AGED_STOCK_POLICY)
    policy["reviewed"] = reviewed
    return policy


def ensure_aged_stock_policy(organization):
    from apps.organizations.models import OrganizationSetting

    setting, _ = OrganizationSetting.objects.get_or_create(
        organization=organization,
        key=AGED_STOCK_POLICY_KEY,
        defaults={
            "value": default_aged_stock_policy(reviewed=False),
            "description": "Organization-wide stock age thresholds.",
        },
    )
    return setting


def get_aged_stock_policy(organization):
    if not organization:
        return default_aged_stock_policy(reviewed=False)
    setting = ensure_aged_stock_policy(organization)
    value = setting.value if isinstance(setting.value, dict) else {}
    policy = default_aged_stock_policy(reviewed=False)
    policy["reviewed"] = bool(value.get("reviewed", False))
    buckets = value.get("buckets")
    if isinstance(buckets, list) and buckets:
        normalized = []
        default_by_code = {bucket["code"]: bucket for bucket in policy["buckets"]}
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            code = bucket.get("code")
            if code not in default_by_code:
                continue
            base = default_by_code[code].copy()
            base.update({
                "min_days": int(bucket.get("min_days", base["min_days"])),
                "max_days": bucket.get("max_days", base["max_days"]),
            })
            if base["max_days"] is not None:
                base["max_days"] = int(base["max_days"])
            normalized.append(base)
        if len(normalized) == len(policy["buckets"]):
            policy["buckets"] = normalized
    return policy


def save_aged_stock_policy(organization, *, fresh_max, aging_max, slow_max, critical_max):
    setting = ensure_aged_stock_policy(organization)
    policy = default_aged_stock_policy(reviewed=True)
    bucket_ranges = {
        "fresh": (0, fresh_max),
        "aging": (fresh_max + 1, aging_max),
        "slow": (aging_max + 1, slow_max),
        "critical": (slow_max + 1, critical_max),
        "attention": (critical_max + 1, None),
    }
    for bucket in policy["buckets"]:
        bucket["min_days"], bucket["max_days"] = bucket_ranges[bucket["code"]]
    setting.value = policy
    setting.description = "Organization-wide stock age thresholds."
    setting.save(update_fields=["value", "description", "updated_at"])
    return setting


def first_aged_stock_day(policy):
    buckets = policy.get("buckets", [])
    for bucket in buckets:
        if bucket.get("code") != "fresh":
            return int(bucket["min_days"])
    return 5


def aged_stock_cutoff(policy, *, now=None):
    now = now or timezone.now()
    return now - timedelta(days=first_aged_stock_day(policy))


def age_days_for(stock_unit, *, today=None):
    today = today or timezone.localdate()
    return max((today - timezone.localtime(stock_unit.created_at).date()).days, 0)


def bucket_for_age(age_days, policy):
    for bucket in policy.get("buckets", []):
        min_days = int(bucket["min_days"])
        max_days = bucket["max_days"]
        if age_days >= min_days and (max_days is None or age_days <= int(max_days)):
            return bucket
    return policy["buckets"][-1]


def bucket_choices(policy, *, include_fresh=False):
    return [
        (bucket["code"], bucket["label"])
        for bucket in policy.get("buckets", [])
        if include_fresh or bucket["code"] != "fresh"
    ]


def active_aged_stock_action_statuses():
    from .models import AgedStockActionStatus

    return [AgedStockActionStatus.REQUESTED, AgedStockActionStatus.APPROVED]


@transaction.atomic
def request_aged_stock_action(*, stock_unit, action_type, reason, next_step="", requested_by):
    from apps.operations.services import request_approval

    from .models import AgedStockAction, AgedStockActionStatus, SerialStatus

    if stock_unit.status != SerialStatus.AVAILABLE:
        raise ValidationError("Only available stock can be submitted for an aged-stock action.")
    existing = AgedStockAction.objects.select_for_update().filter(
        organization=stock_unit.organization,
        stock_unit=stock_unit,
        status__in=active_aged_stock_action_statuses(),
    ).first()
    if existing:
        return existing
    action = AgedStockAction.objects.create(
        organization=stock_unit.organization,
        stock_unit=stock_unit,
        action_type=action_type,
        reason=reason,
        next_step=next_step,
        proposed_by=requested_by,
    )
    approval = request_approval(
        organization=stock_unit.organization,
        request_type="aged_stock_action",
        target=action,
        requested_by=requested_by,
        reason=reason,
        branch=stock_unit.location.branch if stock_unit.location_id else None,
    )
    action.approval = approval
    action.save(update_fields=["approval", "updated_at"])
    return action


@transaction.atomic
def sync_aged_stock_action_from_approval(*, approval):
    from apps.operations.models import ApprovalStatus

    from .models import AgedStockAction, AgedStockActionStatus

    action = AgedStockAction.objects.select_for_update().filter(approval=approval).first()
    if not action:
        return None
    if approval.status == ApprovalStatus.APPROVED:
        action.status = AgedStockActionStatus.APPROVED
        action.approved_by = approval.decided_by
        action.approved_at = approval.decided_at
    elif approval.status == ApprovalStatus.REJECTED:
        action.status = AgedStockActionStatus.REJECTED
        action.approved_by = approval.decided_by
        action.approved_at = approval.decided_at
    action.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    return action
