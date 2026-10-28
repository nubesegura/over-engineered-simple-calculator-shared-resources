"""Run from the repository root: python -m unittest discover -s scripts/tests -v

Offline tests of .github/workflows-local/lib/cfn_local.py (registry normalisation of the ai host and
the change set guard). The helper lives in a git-ignored folder: the module is skipped when it is absent.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

LIB = os.path.join(os.path.dirname(__file__), "..", "..", ".github", "workflows-local", "lib")
sys.path.insert(0, LIB)
try:
    import cfn_local  # noqa: E402
except ImportError:  # pragma: no cover
    cfn_local = None

PREFIX = "/oecalc/dev/api-backends"
REAL_DNS = "d-abc123.execute-api.us-east-2.amazonaws.com"
REAL_ZONE = "ZOJJZC49E0EPZ"


@unittest.skipIf(cfn_local is None, "cfn_local.py is not present (git-ignored local helper)")
class CfnLocalTest(unittest.TestCase):
    def run_registry(self, env):
        saved = {k: v for k, v in os.environ.items() if k.startswith("BACKEND_")}
        for key in saved:
            del os.environ[key]
        os.environ.update(env)
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = cfn_local.main(["registry", "--prefix", PREFIX, "--from-env"])
                except SystemExit as exc:
                    code = exc.code
        finally:
            for key in env:
                os.environ.pop(key, None)
            os.environ.update(saved)
        values = dict(line.split("=", 1) for line in out.getvalue().splitlines() if "=" in line)
        return code, values, err.getvalue()

    def run_guard(self, changes):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "changeset.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"Changes": [{"Type": "Resource", "ResourceChange": c} for c in changes]}, handle)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cfn_local.main(["changeset-guard", path])
            return code, out.getvalue(), err.getvalue()

    # ------------------------------------------------------------------ registry
    def test_ai_none_in_any_case_is_not_published(self):
        for value in ("none", "NONE", " none ", "None"):
            code, values, _ = self.run_registry(
                {"BACKEND_AI_DNS_NAME": value, "BACKEND_AI_HOSTED_ZONE_ID": value})
            self.assertEqual(code, 0, value)
            self.assertEqual(values["AiDnsName"], "", value)
            self.assertEqual(values["AiHostedZoneId"], "", value)

    def test_ai_empty_or_missing(self):
        code, values, _ = self.run_registry({})
        self.assertEqual(code, 0)
        self.assertEqual((values["AiDnsName"], values["AiHostedZoneId"]), ("", ""))

    def test_ai_one_of_two_missing_is_not_a_failure(self):
        code, values, _ = self.run_registry({"BACKEND_AI_DNS_NAME": REAL_DNS})
        self.assertEqual(code, 0)
        self.assertEqual(values["AiDnsName"], REAL_DNS)
        self.assertEqual(values["AiHostedZoneId"], "")
        code, values, _ = self.run_registry({"BACKEND_AI_DNS_NAME": "none", "BACKEND_AI_HOSTED_ZONE_ID": REAL_ZONE})
        self.assertEqual(code, 0)
        self.assertEqual((values["AiDnsName"], values["AiHostedZoneId"]), ("", REAL_ZONE))

    def test_ai_real_pair(self):
        code, values, _ = self.run_registry(
            {"BACKEND_AI_DNS_NAME": REAL_DNS, "BACKEND_AI_HOSTED_ZONE_ID": REAL_ZONE})
        self.assertEqual(code, 0)
        self.assertEqual((values["AiDnsName"], values["AiHostedZoneId"]), (REAL_DNS, REAL_ZONE))

    def test_ai_malformed_dns_name_fails_naming_the_parameter(self):
        code, _, err = self.run_registry(
            {"BACKEND_AI_DNS_NAME": "nodots", "BACKEND_AI_HOSTED_ZONE_ID": REAL_ZONE})
        self.assertNotEqual(code, 0)
        self.assertIn(f"{PREFIX}/ai/dns-name", err)

    def test_ai_malformed_zone_id_fails_naming_the_parameter(self):
        for bad in ("z123", "ABC", "Z12-3", "Z 1"):
            code, _, err = self.run_registry(
                {"BACKEND_AI_DNS_NAME": REAL_DNS, "BACKEND_AI_HOSTED_ZONE_ID": bad})
            self.assertNotEqual(code, 0, bad)
            self.assertIn(f"{PREFIX}/ai/hosted-zone-id", err)

    def test_ai_is_not_a_weighted_backend(self):
        self.assertNotIn("ai", cfn_local.BACKENDS)
        self.assertEqual(cfn_local.BACKENDS, ("sls", "ecs", "eks", "ec2"))
        self.assertNotIn("AiRecord", {f"ApiRecord{b.capitalize()}" for b in cfn_local.BACKENDS})

    def test_ai_published_does_not_change_the_weighted_backends(self):
        base, values_base, _ = self.run_registry({})
        code, values, _ = self.run_registry(
            {"BACKEND_AI_DNS_NAME": REAL_DNS, "BACKEND_AI_HOSTED_ZONE_ID": REAL_ZONE})
        for backend in cfn_local.BACKENDS:
            for suffix in ("DnsName", "HostedZoneId"):
                key = cfn_local.param_name(backend, suffix)
                self.assertEqual(values[key], values_base[key])

    # ------------------------------------------------------------------ change set guard
    @staticmethod
    def change(action, logical, rtype, replacement=None):
        item = {"Action": action, "LogicalResourceId": logical, "ResourceType": rtype}
        if replacement is not None:
            item["Replacement"] = replacement
        return item

    def test_guard_add_of_all_ai_resources_passes(self):
        changes = [
            self.change("Add", "AiCertificate", "AWS::CertificateManager::Certificate"),
            self.change("Add", "AiCertificateArnParameter", "AWS::SSM::Parameter"),
            self.change("Add", "AiCertExpiry30dAlarm", "AWS::CloudWatch::Alarm"),
            self.change("Add", "AiCertExpiry7dAlarm", "AWS::CloudWatch::Alarm"),
            self.change("Add", "AiRecord", "AWS::Route53::RecordSet"),
        ]
        self.assertEqual(self.run_guard(changes)[0], 0)

    def test_guard_remove_of_ai_record_only_passes(self):
        code, out, _ = self.run_guard([self.change("Remove", "AiRecord", "AWS::Route53::RecordSet")])
        self.assertEqual(code, 0)
        self.assertIn("AiRecord", out)

    def test_guard_remove_of_ai_record_and_certificate_fails(self):
        code, _, err = self.run_guard([
            self.change("Remove", "AiRecord", "AWS::Route53::RecordSet"),
            self.change("Remove", "AiCertificate", "AWS::CertificateManager::Certificate"),
        ])
        self.assertEqual(code, 1)
        self.assertIn("AiCertificate", err)

    def test_guard_replacement_of_ai_record_fails(self):
        code, _, err = self.run_guard([self.change("Modify", "AiRecord", "AWS::Route53::RecordSet", "True")])
        self.assertEqual(code, 1)
        self.assertIn("AiRecord", err)

    def test_guard_in_place_update_of_ai_record_passes_like_ws(self):
        for logical in ("AiRecord", "WsRecord"):
            code, _, _ = self.run_guard([self.change("Modify", logical, "AWS::Route53::RecordSet", "False")])
            self.assertEqual(code, 0, logical)

    def test_guard_remove_of_an_ai_alarm_fails(self):
        for days in ("30", "7", "90"):
            code, _, err = self.run_guard(
                [self.change("Remove", f"AiCertExpiry{days}dAlarm", "AWS::CloudWatch::Alarm")])
            self.assertEqual(code, 1, days)
            self.assertIn(f"AiCertExpiry{days}dAlarm", err)

    def test_guard_replacement_of_ai_certificate_fails(self):
        for replacement in ("True", "Conditional"):
            code, _, _ = self.run_guard(
                [self.change("Modify", "AiCertificate", "AWS::CertificateManager::Certificate", replacement)])
            self.assertEqual(code, 1, replacement)

    def test_guard_previous_cases_unchanged(self):
        ok = [
            [self.change("Remove", "WsRecord", "AWS::Route53::RecordSet")],
            [self.change("Remove", "ApiRecordSls", "AWS::Route53::RecordSet")],
            [self.change("Remove", "ApiCertExpiry90dAlarm", "AWS::CloudWatch::Alarm")],
            [self.change("Remove", "WsCertExpiry15dAlarm", "AWS::CloudWatch::Alarm")],
        ]
        for changes in ok:
            self.assertEqual(self.run_guard(changes)[0], 0, changes)
        bad = [
            [self.change("Remove", "WsCertificate", "AWS::CertificateManager::Certificate")],
            [self.change("Remove", "ApiCertExpiry30dAlarm", "AWS::CloudWatch::Alarm")],
            [self.change("Modify", "ApiRecordEcs", "AWS::Route53::RecordSet", "True")],
        ]
        for changes in bad:
            self.assertEqual(self.run_guard(changes)[0], 1, changes)


if __name__ == "__main__":
    unittest.main()
