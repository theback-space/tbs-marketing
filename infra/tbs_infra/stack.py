"""One small EC2 host running the app and Caddy (HTTPS) in Docker.

- No SSH and no key pairs: shell access is through SSM Session Manager.
- App data and TLS certificates live on a separate encrypted EBS volume that survives
  instance replacement and is backed up daily by AWS Backup.
- Secrets (API keys, tokens, dashboard password) live in Secrets Manager and are written
  to the host's .env at deploy time; non-secret settings live in an SSM parameter.
- `cdk deploy` uploads the source, then tells the host (via SSM Run Command) to rebuild.
"""

from __future__ import annotations

import json
from pathlib import Path

from aws_cdk import (
    CfnOutput, Duration, RemovalPolicy, Size, Stack, aws_backup as backup, aws_budgets as budgets, aws_ec2 as ec2,
    aws_events as events, aws_iam as iam, aws_logs as logs, aws_s3_assets as s3_assets,
    aws_secretsmanager as secretsmanager, aws_ssm as ssm, custom_resources as cr,
)
from cdk_nag import NagSuppressions
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_VERSION = "v2.40.3"
COMPOSE_SHA256 = "d26373b19e89160546d15407516cc59f453030d9bc5b43ba7faf16f7b4980137"  # linux-aarch64
CONFIG_PARAM = "/tbs-social/config"
RELEASE_PARAM = "/tbs-social/release"
SECRET_NAME = "tbs-social/env"
SECRET_KEYS = [
    "ANTHROPIC_API_KEY", "META_PAGE_ID", "META_PAGE_ACCESS_TOKEN", "META_IG_USER_ID",
    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN", "GBP_ACCOUNT_ID", "GBP_LOCATION_ID",
]


class SocialStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, domain: str, platforms: str,
                 manual_platforms: str, automation: str, compliance: str, alert_email: str,
                 monthly_budget_usd: float, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- network: public subnet only, no NAT gateway (cost) ---------------------------
        vpc = ec2.Vpc(self, "Vpc", max_azs=1, nat_gateways=0, subnet_configuration=[
            ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24)])
        flow_logs = logs.LogGroup(self, "FlowLogs", retention=logs.RetentionDays.ONE_MONTH,
                                  removal_policy=RemovalPolicy.DESTROY)
        vpc.add_flow_log("FlowLog", destination=ec2.FlowLogDestination.to_cloud_watch_logs(flow_logs),
                         traffic_type=ec2.FlowLogTrafficType.REJECT)

        sg = ec2.SecurityGroup(self, "WebSg", vpc=vpc, allow_all_outbound=True,
                               description="HTTPS dashboard; HTTP only for certificate issuance and redirect")
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "HTTPS")
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "ACME HTTP-01 and redirect to HTTPS")

        # --- secrets and config -----------------------------------------------------------
        secret = secretsmanager.Secret(
            self, "EnvSecret", secret_name=SECRET_NAME,
            description="tbs-social secrets: fill in the empty values in the console",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template=json.dumps({k: "" for k in SECRET_KEYS}),
                generate_string_key="TBS_DASHBOARD_PASSWORD", password_length=24, exclude_punctuation=True),
            removal_policy=RemovalPolicy.RETAIN,
        )
        config = ssm.StringParameter(self, "Config", parameter_name=CONFIG_PARAM, string_value=json.dumps({
            "TBS_DOMAIN": domain, "TBS_PLATFORMS": platforms, "TBS_MANUAL_PLATFORMS": manual_platforms,
            "TBS_AUTOMATION": automation, "TBS_COMPLIANCE": compliance, "TBS_DASHBOARD_USER": "admin",
        }))

        source = s3_assets.Asset(self, "Source", path=str(REPO_ROOT), exclude=[
            ".git", ".env", "*.db", "**/__pycache__", ".pytest_cache", "infra/cdk.out", "infra/.venv",
            "infra/node_modules", "infra/tests", "tests", "*.egg-info"])
        release = ssm.StringParameter(self, "Release", parameter_name=RELEASE_PARAM,
                                      string_value=source.s3_object_url)

        log_group = logs.LogGroup(self, "AppLogs", log_group_name="/tbs-social/app",
                                  retention=logs.RetentionDays.THREE_MONTHS, removal_policy=RemovalPolicy.DESTROY)

        # --- host -----------------------------------------------------------------------
        role = iam.Role(self, "HostRole", assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
                        managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name(
                            "AmazonSSMManagedInstanceCore")])
        source.grant_read(role)
        secret.grant_read(role)
        config.grant_read(role)
        release.grant_read(role)
        log_group.grant_write(role)

        az = vpc.public_subnets[0].availability_zone
        data_volume = ec2.Volume(self, "DataVolume", availability_zone=az, size=Size.gibibytes(8),
                                 volume_type=ec2.EbsDeviceVolumeType.GP3, encrypted=True,
                                 removal_policy=RemovalPolicy.RETAIN)

        user_data = ec2.UserData.for_linux()
        bootstrap = (Path(__file__).parent.parent / "instance" / "bootstrap.sh").read_text()
        user_data.add_commands(
            f"export TBS_DATA_VOLUME_ID={data_volume.volume_id}",
            f"export TBS_REGION={self.region}",
            f"export COMPOSE_VERSION={COMPOSE_VERSION} COMPOSE_SHA256={COMPOSE_SHA256}",
            f"export TBS_SECRET_ARN={secret.secret_arn}",
            f"export TBS_LOG_GROUP={log_group.log_group_name}",
            bootstrap,
        )

        # User data runs once, on first boot. Later releases arrive through tbs-deploy (below).
        # Replacing the instance needs care: detach the data volume first (see README).
        host = ec2.Instance(
            self, "Host", vpc=vpc, vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            instance_type=ec2.InstanceType("t4g.micro"),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(
                cpu_type=ec2.AmazonLinuxCpuType.ARM_64, cached_in_context=True),
            security_group=sg, role=role, user_data=user_data, require_imdsv2=True,
            block_devices=[ec2.BlockDevice(device_name="/dev/xvda", volume=ec2.BlockDeviceVolume.ebs(
                16, encrypted=True, volume_type=ec2.EbsDeviceVolumeType.GP3))],
            detailed_monitoring=False,
        )
        ec2.CfnVolumeAttachment(self, "DataAttachment", instance_id=host.instance_id,
                                volume_id=data_volume.volume_id, device="/dev/sdf")
        eip = ec2.CfnEIP(self, "Ip", domain="vpc", instance_id=host.instance_id)

        # --- redeploy the host whenever the source changes -----------------------------
        run_shell = self.format_arn(service="ssm", account="", resource="document",
                                    resource_name="AWS-RunShellScript")
        instance_arn = self.format_arn(service="ec2", resource="instance", resource_name=host.instance_id)
        redeploy = cr.AwsCustomResource(
            self, "Redeploy",
            on_create=cr.AwsSdkCall(  # the host deploys itself on first boot; nothing to do here
                service="SSM", action="getParameter", parameters={"Name": RELEASE_PARAM},
                physical_resource_id=cr.PhysicalResourceId.of("tbs-social-redeploy")),
            on_update=cr.AwsSdkCall(
                service="SSM", action="sendCommand", physical_resource_id=cr.PhysicalResourceId.of(
                    f"tbs-social-redeploy-{source.asset_hash}"),
                parameters={"DocumentName": "AWS-RunShellScript", "InstanceIds": [host.instance_id],
                            "Comment": f"tbs-social release {source.asset_hash[:12]}",
                            "Parameters": {"commands": ["/usr/local/bin/tbs-deploy"]},
                            "CloudWatchOutputConfig": {"CloudWatchOutputEnabled": True,
                                                       "CloudWatchLogGroupName": log_group.log_group_name}}),
            policy=cr.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(actions=["ssm:SendCommand"], resources=[run_shell, instance_arn]),
                iam.PolicyStatement(actions=["ssm:GetParameter"], resources=[release.parameter_arn]),
            ]),
            install_latest_aws_sdk=False,
        )
        redeploy.node.add_dependency(release, host)

        # --- backups and cost guardrail ---------------------------------------------------
        plan = backup.BackupPlan(self, "Backups", backup_plan_rules=[backup.BackupPlanRule(
            rule_name="daily-14d", schedule_expression=events.Schedule.cron(hour="7", minute="0"),
            delete_after=Duration.days(14))])
        plan.add_selection("DataVolume", resources=[backup.BackupResource.from_arn(self.format_arn(
            service="ec2", resource="volume", resource_name=data_volume.volume_id))])

        if alert_email:
            budgets.CfnBudget(self, "Budget", budget=budgets.CfnBudget.BudgetDataProperty(
                budget_type="COST", time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=monthly_budget_usd, unit="USD")),
                notifications_with_subscribers=[
                    budgets.CfnBudget.NotificationWithSubscribersProperty(
                        notification=budgets.CfnBudget.NotificationProperty(
                            comparison_operator="GREATER_THAN", notification_type=kind,
                            threshold=80 if kind == "ACTUAL" else 100, threshold_type="PERCENTAGE"),
                        subscribers=[budgets.CfnBudget.SubscriberProperty(
                            address=alert_email, subscription_type="EMAIL")])
                    for kind in ("ACTUAL", "FORECASTED")])

        CfnOutput(self, "PublicIp", value=eip.attr_public_ip,
                  description=f"Point an A record for {domain} at this address")
        CfnOutput(self, "DashboardUrl", value=f"https://{domain}")
        CfnOutput(self, "SecretName", value=SECRET_NAME,
                  description="Fill in API keys here (Secrets Manager); the dashboard password is generated")
        CfnOutput(self, "ShellCommand", value=f"aws ssm start-session --target {host.instance_id} "
                                              f"--region {self.region}")
        CfnOutput(self, "RedeployCommand", value=(
            f"aws ssm send-command --region {self.region} --instance-ids {host.instance_id} "
            "--document-name AWS-RunShellScript --parameters commands=/usr/local/bin/tbs-deploy"))

        self._suppress_nag(role, sg, secret)

    def _suppress_nag(self, role: iam.Role, sg: ec2.SecurityGroup, secret: secretsmanager.Secret) -> None:
        NagSuppressions.add_resource_suppressions(sg, [{
            "id": "AwsSolutions-EC23",
            "reason": "Public HTTPS dashboard; port 80 is required for Let's Encrypt HTTP-01 and redirects."}])
        NagSuppressions.add_resource_suppressions(role, apply_to_children=True, suppressions=[
            {"id": "AwsSolutions-IAM4", "reason": "AmazonSSMManagedInstanceCore is the AWS-supported policy "
                                                  "for Session Manager (replaces SSH).",
             "applies_to": ["Policy::arn:<AWS::Partition>:iam::aws:policy/AmazonSSMManagedInstanceCore"]},
            {"id": "AwsSolutions-IAM5", "reason": "Read-only s3:GetObject*/List* from CDK's asset grant on "
                                                  "the bootstrap asset bucket, which in this dedicated "
                                                  "account holds only this project's releases."},
        ])
        NagSuppressions.add_resource_suppressions(secret, [{
            "id": "AwsSolutions-SMG4",
            "reason": "Third-party API tokens (Meta, Google, Anthropic) are rotated in their own consoles."}])
        NagSuppressions.add_stack_suppressions(self, [
            {"id": "AwsSolutions-EC28", "reason": "Single low-traffic host; detailed monitoring not needed."},
            {"id": "AwsSolutions-EC29", "reason": "Instance is disposable; data lives on a retained, "
                                                  "backed-up EBS volume."},
            {"id": "AwsSolutions-IAM4", "reason": "CDK-managed Lambda for the redeploy custom resource uses "
                                                  "AWSLambdaBasicExecutionRole.",
             "applies_to": ["Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/"
                            "AWSLambdaBasicExecutionRole"]},
            {"id": "AwsSolutions-L1", "reason": "Runtime of the CDK-provided custom resource Lambda is "
                                                "managed by aws-cdk-lib."},
        ])
