"""Run from the repository root: python -m unittest discover -s scripts/tests -v"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import plan_guard  # noqa: E402


def rec(key, actions, after_sid=None):
    before = {"set_identifier": key, "weighted_routing_policy": [{"weight": 0}]}
    after = {"set_identifier": after_sid or key, "weighted_routing_policy": [{"weight": 0}]}
    return {"address": f'aws_route53_record.api["{key}"]', "type": "aws_route53_record", "index": key,
            "change": {"actions": actions, "before": None if actions == ["create"] else before,
                       "after": None if actions == ["delete"] else after}}


def other(address, rtype, actions):
    return {"address": address, "type": rtype, "change": {"actions": actions, "before": {}, "after": {}}}


def ws_rec(actions, name="ws.example.test"):
    before = {"name": name, "type": "A"}
    return {"address": "aws_route53_record.ws[0]", "type": "aws_route53_record", "index": 0,
            "change": {"actions": actions, "before": None if actions == ["create"] else before,
                       "after": None if actions == ["delete"] else before}}


class PlanGuardTest(unittest.TestCase):
    def run_cmd(self, command, content):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "plan.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content if isinstance(content, str) else json.dumps(content))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = plan_guard.main([command, path])
        return code, out.getvalue()

    def plan(self, *changes):
        return {"resource_changes": list(changes)}

    def test_nothing_to_delete_passes(self):
        code, out = self.run_cmd("check", self.plan(
            rec("eks", ["update"]), rec("ecs", ["no-op"]),
            other("aws_acm_certificate.api", "aws_acm_certificate", ["no-op"])))
        self.assertEqual(code, 0)
        self.assertIn("No delete", out)

    def test_empty_plan_passes(self):
        self.assertEqual(self.run_cmd("check", {})[0], 0)

    def test_retire_unpublished_backend_passes(self):
        code, out = self.run_cmd("check", self.plan(rec("sls", ["delete"]), rec("eks", ["no-op"])))
        self.assertEqual(code, 0)
        self.assertIn("- sls:", out)

    def test_certificate_delete_fails(self):
        code, out = self.run_cmd("check", self.plan(
            other("aws_acm_certificate.api", "aws_acm_certificate", ["delete"])))
        self.assertEqual(code, 1)
        self.assertIn("aws_acm_certificate.api", out)

    def test_certificate_replacement_fails_even_with_retire(self):
        code, _ = self.run_cmd("check", self.plan(
            rec("sls", ["delete"]), other("aws_acm_certificate.api", "aws_acm_certificate", ["create", "delete"])))
        self.assertEqual(code, 1)

    def test_validation_record_delete_fails(self):
        code, _ = self.run_cmd("check", self.plan(
            other('aws_route53_record.validation["x"]', "aws_route53_record", ["delete"])))
        self.assertEqual(code, 1)

    def test_record_replacement_fails(self):
        for actions in (["delete", "create"], ["create", "delete"]):
            self.assertEqual(self.run_cmd("check", self.plan(rec("eks", actions)))[0], 1)

    def test_delete_of_record_still_in_plan_fails(self):
        # the same set_identifier is kept by another address
        code, _ = self.run_cmd("check", self.plan(rec("eks", ["delete"]), rec("eks-new", ["update"], after_sid="eks")))
        self.assertEqual(code, 1)

    def test_retire_ws_only_passes(self):
        code, out = self.run_cmd("check", self.plan(ws_rec(["delete"]), rec("eks", ["no-op"])))
        self.assertEqual(code, 0)
        self.assertIn("- ws:", out)

    def test_retire_ws_with_certificate_delete_fails(self):
        code, _ = self.run_cmd("check", self.plan(
            ws_rec(["delete"]), other("aws_acm_certificate.ws[0]", "aws_acm_certificate", ["delete"])))
        self.assertEqual(code, 1)

    def test_retire_api_record_still_passes(self):
        self.assertEqual(self.run_cmd("check", self.plan(rec("sls", ["delete"]), ws_rec(["no-op"])))[0], 0)

    def test_ws_record_replacement_fails(self):
        for actions in (["delete", "create"], ["create", "delete"]):
            self.assertEqual(self.run_cmd("check", self.plan(ws_rec(actions)))[0], 1)

    def test_ws_delete_also_updated_in_plan_fails(self):
        code, _ = self.run_cmd("check", self.plan(ws_rec(["delete"]), ws_rec(["update"])))
        self.assertEqual(code, 1)

    def test_ws_parameter_alarm_and_validation_delete_fail(self):
        for addr, rtype in (("aws_ssm_parameter.ws_certificate_arn[0]", "aws_ssm_parameter"),
                            ('aws_cloudwatch_metric_alarm.ws_certificate_expiry["30"]', "aws_cloudwatch_metric_alarm"),
                            ("aws_acm_certificate_validation.ws[0]", "aws_acm_certificate_validation")):
            self.assertEqual(self.run_cmd("check", self.plan(ws_rec(["delete"]), other(addr, rtype, ["delete"])))[0], 1)

    def test_retired_expiry_alarms_pass(self):
        alarms = [other(f'aws_cloudwatch_metric_alarm.{name}["{days}"]', "aws_cloudwatch_metric_alarm", ["delete"])
                  for name in ("certificate_expiry", "ws_certificate_expiry") for days in ("90", "60", "15")]
        code, out = self.run_cmd("check", self.plan(
            *alarms, other('aws_cloudwatch_metric_alarm.certificate_expiry["30"]', "aws_cloudwatch_metric_alarm", ["update"])))
        self.assertEqual(code, 0)
        self.assertIn("### Retired expiry alarms", out)
        self.assertIn('certificate_expiry["60"]', out)

    def test_kept_expiry_alarm_delete_fails(self):
        for days in ("30", "7"):
            for name in ("certificate_expiry", "ws_certificate_expiry"):
                alarm = other(f'aws_cloudwatch_metric_alarm.{name}["{days}"]', "aws_cloudwatch_metric_alarm", ["delete"])
                self.assertEqual(self.run_cmd("check", self.plan(alarm))[0], 1)

    def test_retired_alarm_replacement_or_other_alarm_fails(self):
        replaced = other('aws_cloudwatch_metric_alarm.certificate_expiry["90"]', "aws_cloudwatch_metric_alarm",
                         ["delete", "create"])
        self.assertEqual(self.run_cmd("check", self.plan(replaced))[0], 1)
        stranger = other('aws_cloudwatch_metric_alarm.other["90"]', "aws_cloudwatch_metric_alarm", ["delete"])
        self.assertEqual(self.run_cmd("check", self.plan(stranger))[0], 1)

    def test_retired_alarm_with_certificate_delete_fails(self):
        code, _ = self.run_cmd("check", self.plan(
            other('aws_cloudwatch_metric_alarm.certificate_expiry["90"]', "aws_cloudwatch_metric_alarm", ["delete"]),
            other("aws_acm_certificate.api", "aws_acm_certificate", ["delete"])))
        self.assertEqual(code, 1)

    def test_summary_ignores_retired_alarms(self):
        code, out = self.run_cmd("summary", self.plan(
            other('aws_cloudwatch_metric_alarm.certificate_expiry["90"]', "aws_cloudwatch_metric_alarm", ["delete"])))
        self.assertEqual(code, 0)
        self.assertIn("Other drift:** none", out)

    def test_summary_classifies_ws(self):
        code, out = self.run_cmd("summary", self.plan(ws_rec(["create"])))
        self.assertEqual(code, 0)
        self.assertIn("Backend new (records to add):** ws.example.test", out)
        self.assertIn("Other drift:** none", out)
        code, out = self.run_cmd("summary", self.plan(ws_rec(["delete"])))
        self.assertIn("Backend retired (records to remove):** ws.example.test", out)
        self.assertIn("Other drift:** none", out)

    def test_malformed_json_fails(self):
        self.assertEqual(self.run_cmd("check", "{not json")[0], 1)
        self.assertEqual(self.run_cmd("check", "[]")[0], 1)
        self.assertEqual(self.run_cmd("check", {"resource_changes": [{"address": "x"}]})[0], 1)

    def test_summary_classifies(self):
        weight_change = rec("ecs", ["update"])
        weight_change["change"]["after"]["weighted_routing_policy"] = [{"weight": 5}]
        code, out = self.run_cmd("summary", self.plan(
            rec("sls", ["delete"]), rec("ec2", ["create"]), weight_change,
            other("terraform_data.weights_guard", "terraform_data", ["update"]),
            other("aws_sns_topic.x", "aws_sns_topic", ["update"])))
        self.assertEqual(code, 0)
        self.assertIn("Backend retired (records to remove):** sls", out)
        self.assertIn("Backend new (records to add):** ec2", out)
        self.assertIn("Weights changed:** ecs", out)
        self.assertIn("aws_sns_topic.x (update)", out)


if __name__ == "__main__":
    unittest.main()
