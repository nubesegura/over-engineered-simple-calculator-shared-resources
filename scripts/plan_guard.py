#!/usr/bin/env python3
"""Plan guard and plan classifier for the API edge (reads `terraform show -json` output, offline).

check    Owner rule (2026-10-06): a plan that deletes or replaces anything is refused, EXCEPT a pure
         delete (actions exactly ["delete"]) of an aws_route53_record.api[...] whose set_identifier is
         not kept by any other record of the plan. That is a backend that is no longer published.
         One more exception (spec 002): the pure delete of the single `ws` host record (WS_RECORD_ADDRESS),
         unless the same plan also creates or updates it.
         One more exception (owner decision 2026-10-26, ACM-03 now 30 and 7 days): the pure delete of the
         retired expiry alarms (thresholds 90, 60 and 15) of the api or ws certificate. The 30 and 7 day
         alarms are never deletable.
         Exit 0 = allowed, 1 = refused or unreadable plan. Retired backends go to stdout (markdown).
summary  Classifies a non-empty plan for the drift check: backend retired, backend new, weights
         changed, other drift. Exit 0 on a readable plan, 1 on an unreadable one.
"""
import argparse
import json
import sys

RECORD_PREFIX = "aws_route53_record.api["
WEIGHTS_GUARD = "terraform_data.weights_guard"
# The one simple alias record of the WebSocket host. Keep in sync with modules/api-hostname/ws.tf.
WS_RECORD_ADDRESS = "aws_route53_record.ws[0]"
WS_LABEL = "ws"
# Expiry alarms retired when ACM-03 went from 90/60/30/15 to 30/7 days. Keep in sync with
# modules/api-hostname (monitoring.tf, ws.tf). Only these keys may be deleted.
ALARM_TYPE = "aws_cloudwatch_metric_alarm"
ALARM_PREFIXES = ("aws_cloudwatch_metric_alarm.certificate_expiry[", "aws_cloudwatch_metric_alarm.ws_certificate_expiry[")
RETIRED_ALARM_KEYS = ("90", "60", "15")


class PlanError(Exception):
    pass


def load_changes(path):
    try:
        with open(path, encoding="utf-8") as handle:
            plan = json.load(handle)
    except (OSError, ValueError) as exc:
        raise PlanError(f"Could not read the saved plan as JSON: {exc}")
    if not isinstance(plan, dict):
        raise PlanError("The plan JSON is not an object")
    changes = plan.get("resource_changes", [])
    if not isinstance(changes, list):
        raise PlanError("resource_changes is not a list")
    for item in changes:
        if not isinstance(item, dict) or not isinstance(item.get("change"), dict):
            raise PlanError("A resource_changes entry has no change object")
        if not isinstance(item["change"].get("actions"), list):
            raise PlanError("A resource_changes entry has no actions list")
    return changes


def is_record(item):
    return item.get("type") == "aws_route53_record" and str(item.get("address", "")).startswith(RECORD_PREFIX)


def is_ws_record(item):
    return item.get("type") == "aws_route53_record" and item.get("address") == WS_RECORD_ADDRESS


def ws_host(item):
    """Host name of the ws record (from the plan) for the job summary; falls back to the label."""
    for side in ("before", "after"):
        name = (item["change"].get(side) or {}).get("name")
        if isinstance(name, str) and name:
            return name
    return WS_LABEL


def is_retired_alarm(item):
    """Pure delete candidate: an expiry alarm of a retired threshold (api or ws certificate)."""
    address = str(item.get("address", ""))
    if item.get("type") != ALARM_TYPE or item["change"]["actions"] != ["delete"]:
        return False
    return any(address in (f'{prefix}"{key}"]' for key in RETIRED_ALARM_KEYS) for prefix in ALARM_PREFIXES)


def set_id(item, side):
    """set_identifier of a record; falls back to the for_each key (set_identifier = each.key)."""
    value = (item["change"].get(side) or {}).get("set_identifier")
    if isinstance(value, str) and value:
        return value
    index = item.get("index")
    return index if isinstance(index, str) and index else None


def classify(changes):
    """Returns (retired, kept, others): retired = {set_id: address} of pure record deletes,
    kept = set ids of records that remain, others = addresses of every other delete."""
    retired, kept, others = {}, set(), []
    for item in changes:
        actions = item["change"]["actions"]
        address = item.get("address", "?")
        if is_ws_record(item):
            if actions == ["delete"]:
                retired[WS_LABEL] = address
            elif "delete" in actions:
                others.append(address)
            elif actions not in (["read"], ["no-op"]):
                kept.add(WS_LABEL)
        elif is_retired_alarm(item):
            continue  # reported by retired_alarms()
        elif is_record(item) and actions == ["delete"]:
            sid = set_id(item, "before")
            if sid is None:
                others.append(address)
            else:
                retired[sid] = address
        elif "delete" in actions:
            others.append(address)
        elif is_record(item) and actions != ["read"]:
            sid = set_id(item, "after")
            if sid is not None:
                kept.add(sid)
    return retired, kept, others


def retired_alarms(changes):
    return sorted(item["address"] for item in changes if is_retired_alarm(item))


def check(path):
    changes = load_changes(path)
    alarms = retired_alarms(changes)
    retired, kept, others = classify(changes)
    refused = list(others)
    allowed = {}
    for sid, address in retired.items():
        if sid in kept:
            refused.append(f"{address} (backend '{sid}' is still in the plan)")
        else:
            allowed[sid] = address
    if refused:
        print(f"::error::The plan deletes or replaces: {', '.join(refused)}")
        return 1
    if allowed or alarms:
        if allowed:
            print("### Retired backends (records removed)")
            for sid in sorted(allowed):
                print(f"- {sid}: {allowed[sid]}")
            print("")
        if alarms:
            print("### Retired expiry alarms (removed)")
            for address in alarms:
                print(f"- {address}")
            print("")
        print("No other delete and no replacement in the saved plan")
    else:
        print("No delete and no replacement in the saved plan")
    return 0


def only_weight_changed(before, after):
    key = "weighted_routing_policy"
    return before.get(key) != after.get(key) and \
        {k: v for k, v in before.items() if k != key} == {k: v for k, v in after.items() if k != key}


def summary(path):
    changes = load_changes(path)
    retired, _, _ = classify(changes)
    alarms = set(retired_alarms(changes))
    retired_names = [ws_host(i) for i in changes if is_ws_record(i) and i["change"]["actions"] == ["delete"]]
    retired_names += [sid for sid in retired if sid != WS_LABEL]
    new, weights, other = [], [], []
    for item in changes:
        actions = item["change"]["actions"]
        if actions in (["no-op"], ["read"]):
            continue
        address = item.get("address", "?")
        if address in alarms:
            continue  # retired expiry alarm: reported by check
        if is_ws_record(item):
            if actions == ["create"]:
                new.append(ws_host(item))
            elif actions != ["delete"]:
                other.append(f"{address} ({'+'.join(actions)})")
            continue
        elif is_record(item):
            if actions == ["delete"]:
                continue  # listed as retired
            if actions == ["create"]:
                new.append(set_id(item, "after") or address)
                continue
            if actions == ["update"] and only_weight_changed(item["change"].get("before") or {},
                                                             item["change"].get("after") or {}):
                weights.append(set_id(item, "after") or address)
                continue
        elif address == WEIGHTS_GUARD:
            continue  # its input is the sum of the weights: it follows the records
        other.append(f"{address} ({'+'.join(actions)})")
    print("### Drift classification")
    for title, rows in (("Backend retired (records to remove)", sorted(retired_names)),
                        ("Backend new (records to add)", sorted(new)),
                        ("Weights changed", sorted(weights)),
                        ("Other drift", sorted(other))):
        print(f"- **{title}:** {', '.join(rows) if rows else 'none'}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["check", "summary"])
    parser.add_argument("plan_json")
    args = parser.parse_args(argv)
    try:
        return check(args.plan_json) if args.command == "check" else summary(args.plan_json)
    except PlanError as exc:
        print(f"::error::{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
