"""Kill switch for the project account.

Subscribed to the budget-alert SNS topic in the management account. When the
whole-project cap is breached, terminate every EC2 instance in this account and
log what happened. Instances are terminated, not stopped: a stopped instance
keeps its volumes and can be started again, and the point of the cap is that
spending ends.

The org stack publishes only the cap budget's 100% actual-spend notification
and the budget action's own notice to the topic, so any message here should
mean the cap was reached. A message is still inspected before acting, but only
to ignore what is positively recognised as a forecast or as another budget;
there is no threshold arithmetic, because the notification text renders the
threshold as a percentage ("100%") while the budgeted amount is in dollars,
and comparing the two would reject the real breach. Anything else, including a
message whose format is not recognised, is treated as a breach: a kill switch
that silently never fires is worse than one that fires early.

Direct invocation (no SNS records) always terminates, and ``{"dry_run": true}``
only reports what would be terminated.
"""

import logging
import os
import re
import time

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger()
log.setLevel(logging.INFO)

ec2 = boto3.client("ec2")

BUDGET_NAME = os.environ.get("BUDGET_NAME", "")

# Everything that is or will be billed. Terminated and shutting-down instances
# need no action.
LIVE_STATES = ["pending", "running", "stopping", "stopped"]

# Both attributes block TerminateInstances, and each needs its own call.
PROTECTION_ATTRIBUTES = ("DisableApiStop", "DisableApiTermination")
PROTECTION_RETRIES = 4

# Labelled lines in an AWS Budgets notification, e.g. "Alert Type: ACTUAL".
_FIELD = re.compile(
    r"^\s*(?P<key>Budget Name|Alert Type)\s*:\s*(?P<value>.+?)\s*$",
    re.MULTILINE,
)


def parse_alert(message):
    """Return the labelled fields of a budget notification, keyed by label."""
    return {m["key"]: m["value"] for m in _FIELD.finditer(message or "")}


def cap_breached(message):
    """Decide whether one SNS message means the project cap was exceeded.

    Returns (breached, reason). See the module docstring for the policy.
    """
    fields = parse_alert(message)
    if not fields:
        return True, "message format not recognised, treating it as a breach"

    name = fields.get("Budget Name")
    if BUDGET_NAME and name and name != BUDGET_NAME:
        return False, f"alert is for budget {name!r}, not {BUDGET_NAME!r}"

    if fields.get("Alert Type", "").upper() == "FORECASTED":
        return False, "forecast alert, not actual spend"

    return True, "actual spend reached the cap"


def live_instance_ids():
    """Every instance in the account and region that is not already going away."""
    ids = []
    paginator = ec2.get_paginator("describe_instances")
    for page in paginator.paginate(
        Filters=[{"Name": "instance-state-name", "Values": LIVE_STATES}]
    ):
        for reservation in page["Reservations"]:
            ids.extend(i["InstanceId"] for i in reservation["Instances"])
    return ids


def terminate(instance_id):
    """Terminate one instance, clearing stop and termination protection if set.

    One instance per call: TerminateInstances is all-or-nothing on a bad ID and
    per-availability-zone on protected instances, so a batch could leave
    instances running because of one that is protected.
    """
    for attempt in range(PROTECTION_RETRIES):
        try:
            response = ec2.terminate_instances(InstanceIds=[instance_id])
        except ClientError as err:
            if err.response["Error"]["Code"] != "OperationNotPermitted":
                raise
            log.warning("%s is protected; clearing stop and termination protection", instance_id)
            for attribute in PROTECTION_ATTRIBUTES:
                ec2.modify_instance_attribute(InstanceId=instance_id, **{attribute: {"Value": False}})
            # Attribute changes are eventually consistent.
            time.sleep(1 + 2 * attempt)
            continue
        for change in response["TerminatingInstances"]:
            log.info(
                "%s: %s -> %s",
                change["InstanceId"],
                change["PreviousState"]["Name"],
                change["CurrentState"]["Name"],
            )
        return
    raise RuntimeError(f"{instance_id} is still protected after {PROTECTION_RETRIES} attempts")


def handler(event, _context):
    dry_run = False
    records = event.get("Records")
    if records:
        verdicts = [cap_breached(r.get("Sns", {}).get("Message")) for r in records]
        for breached, reason in verdicts:
            log.info("budget alert: %s (%s)", reason, "acting" if breached else "ignoring")
        if not any(breached for breached, _ in verdicts):
            return {"terminated": [], "failed": [], "dry_run": False, "acted": False}
    else:
        dry_run = bool(event.get("dry_run"))

    ids = live_instance_ids()
    log.info("kill switch fired%s: %d instance(s) %s", " (dry run)" if dry_run else "", len(ids), ids)

    result = {"terminated": [], "failed": [], "dry_run": dry_run, "acted": True}
    for instance_id in ids:
        if dry_run:
            continue
        try:
            terminate(instance_id)
            result["terminated"].append(instance_id)
        except (ClientError, RuntimeError):
            log.exception("could not terminate %s", instance_id)
            result["failed"].append(instance_id)

    log.info("kill switch done: %s", result)
    if result["failed"]:
        # Fail the invocation so the error shows up in Lambda metrics and the
        # asynchronous retry has another go at the remaining instances.
        raise RuntimeError(f"{len(result['failed'])} instance(s) could not be terminated: {result['failed']}")
    return result
