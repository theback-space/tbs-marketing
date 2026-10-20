#!/usr/bin/env python3
"""CDK app for the social media manager. Deploy only into the dedicated marketing account."""

import os

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks

from tbs_infra.stack import SocialStack

app = cdk.App()
ctx = app.node.try_get_context
SocialStack(
    app, "TbsSocial",
    env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=ctx("region")),
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
