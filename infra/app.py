#!/usr/bin/env python3
"""CDK app for the social media manager. Deploy only into the dedicated marketing account."""

import os

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks

from tbs_infra.stack import SocialStack

app = cdk.App()
ctx = app.node.try_get_context

# Hard stop if the active AWS login isn't the dedicated marketing account. The CDK CLI sets
# CDK_DEFAULT_ACCOUNT from the current credentials; pinning `account` below also makes
# bootstrap/deploy fail rather than fall back to whatever account the profile points at.
account = ctx("accountId")
active = os.environ.get("CDK_DEFAULT_ACCOUNT")
if active and active != account:
    raise SystemExit(
        f"Refusing to run: logged in to AWS account {active}, but this app only deploys to "
        f"theback-marketing ({account}). Use `--profile tbs-marketing` and check it with "
        "`aws sts get-caller-identity --profile tbs-marketing`.")

SocialStack(
    app, "TbsSocial",
    env=cdk.Environment(account=account, region=ctx("region")),
    domain=ctx("domain"),
    platforms=ctx("platforms"),
    manual_platforms=ctx("manualPlatforms"),
    automation=ctx("automation"),
    compliance=ctx("compliance"),
    alert_email=ctx("alertEmail"),
    monthly_budget_usd=float(ctx("monthlyBudgetUsd")),
)
cdk.Tags.of(app).add("Project", "tbs-social")
cdk.Tags.of(app).add("DataClassification", "Public")
cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))
app.synth()
