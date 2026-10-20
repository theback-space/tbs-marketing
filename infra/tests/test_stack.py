"""Synthesize the stack offline and check its security posture (no AWS credentials needed)."""

import json
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Annotations, Match, Template
from cdk_nag import AwsSolutionsChecks

from tbs_infra.stack import SocialStack

CONTEXT = json.loads((Path(__file__).parents[1] / "cdk.json").read_text())["context"]


@pytest.fixture(scope="module")
def stack():
    app = cdk.App(context=CONTEXT)
    s = SocialStack(
        app, "TbsSocial", env=cdk.Environment(account="111111111111", region=CONTEXT["region"]),
        domain=CONTEXT["domain"], platforms=CONTEXT["platforms"], manual_platforms=CONTEXT["manualPlatforms"],
        automation=CONTEXT["automation"], compliance=CONTEXT["compliance"], alert_email=CONTEXT["alertEmail"],
        monthly_budget_usd=float(CONTEXT["monthlyBudgetUsd"]))
    cdk.Aspects.of(app).add(AwsSolutionsChecks())
    app.synth()
    return s


def test_passes_aws_solutions_checks(stack):
    errors = Annotations.from_stack(stack).find_error("*", Match.string_like_regexp("AwsSolutions-.*"))
    assert [f"{e.id}: {e.entry.data}"[:200] for e in errors] == []


def test_no_ssh_and_only_web_ports_open(stack):
    t = Template.from_stack(stack)
    [sg] = t.find_resources("AWS::EC2::SecurityGroup").values()
    ports = sorted(r["FromPort"] for r in sg["Properties"]["SecurityGroupIngress"])
    assert ports == [80, 443]
    instance = next(iter(t.find_resources("AWS::EC2::Instance").values()))["Properties"]
    assert "KeyName" not in instance
    [lt] = t.find_resources("AWS::EC2::LaunchTemplate").values()
    assert lt["Properties"]["LaunchTemplateData"]["MetadataOptions"]["HttpTokens"] == "required"  # IMDSv2


def test_storage_encrypted_and_data_retained(stack):
    t = Template.from_stack(stack)
    t.has_resource("AWS::EC2::Volume", {"Properties": {"Encrypted": True}, "DeletionPolicy": "Retain"})
    t.has_resource("AWS::SecretsManager::Secret", {"DeletionPolicy": "Retain"})
    t.resource_count_is("AWS::Backup::BackupPlan", 1)
    t.resource_count_is("AWS::EC2::NatGateway", 0)


def test_secret_template_has_every_key_the_app_reads(stack):
    t = Template.from_stack(stack)
    [secret] = t.find_resources("AWS::SecretsManager::Secret").values()
    gen = secret["Properties"]["GenerateSecretString"]
    keys = set(json.loads(gen["SecretStringTemplate"])) | {gen["GenerateStringKey"]}
    env_example = (Path(__file__).parents[2] / ".env.example").read_text()
    for key in keys:
        assert f"\n{key}=" in env_example, key


def test_redeploy_command_is_scoped(stack):
    t = Template.from_stack(stack)
    policies = t.find_resources("AWS::IAM::Policy")
    send = [s for p in policies.values() for s in p["Properties"]["PolicyDocument"]["Statement"]
            if s["Action"] == "ssm:SendCommand"]
    assert len(send) == 1 and "*" not in json.dumps(send[0]["Resource"])
