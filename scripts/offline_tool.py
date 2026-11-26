"""Local-only SAM/lint entry point: no network, credentials, or AWS API calls."""
import os
import socket
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
state = root / ".local-verification" / "tool-state"
state.mkdir(parents=True, exist_ok=True)
os.environ.update(
    AWS_ACCESS_KEY_ID="testing",
    AWS_SECRET_ACCESS_KEY="testing",
    AWS_SESSION_TOKEN="testing",
    AWS_DEFAULT_REGION="us-east-1",
    AWS_EC2_METADATA_DISABLED="true",
    AWS_CONFIG_FILE="NUL",
    AWS_SHARED_CREDENTIALS_FILE="NUL",
    SAM_CLI_TELEMETRY="0",
    PYTHONDONTWRITEBYTECODE="1",
    APPDATA=str(state),
    LOCALAPPDATA=str(state),
    TEMP=str(state),
    TMP=str(state),
)
os.environ.pop("AWS_PROFILE", None)
sys.dont_write_bytecode = True


def blocked(*args, **kwargs):
    raise RuntimeError("Network and AWS API calls prohibited for local validation")


socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked
socket.getaddrinfo = blocked
from botocore.client import BaseClient  # noqa: E402

BaseClient._make_api_call = blocked
tool = sys.argv.pop(1)
if tool == "sam":
    from samcli.cli.main import cli

    cli()
elif tool == "lint":
    from cfnlint.runner import main

    main()
elif tool == "transform":
    import copy
    import json
    import yaml
    import boto3
    from samtranslator.translator.translator import Translator
    from samtranslator.parser.parser import Parser

    template = yaml.safe_load((root / "template.yaml").read_text(encoding="utf-8"))
    template["Description"] = "SilentLoss F1 Transactional Outbox architecture"
    for resource in template["Resources"].values():
        if resource["Type"] == "AWS::Serverless::Function":
            resource["Properties"]["CodeUri"] = {
                "Bucket": "local-validation-placeholder",
                "Key": "not-uploaded.zip",
            }
    outputs = []
    for enabled in ("false", "true"):
        translated = Translator({}, Parser(), boto_session=boto3.Session(region_name="us-east-1")).translate(
            copy.deepcopy(template),
            {"EnableFaultAdapter": enabled, "BenchmarkControllerArn": "local-test-controller"},
        )
        resources = translated["Resources"]
        roles = {k: v for k, v in resources.items() if v["Type"] == "AWS::IAM::Role"}
        expected_roles = {
            "OrderServiceRole",
            "OutboxRelayRole",
            "OutboxRecoverySweeperRole",
            "FulfillmentWorkerRole",
            "ExperimentServiceRole",
        }
        assert set(roles) == expected_roles, set(roles)
        statements = []
        for role_name, role in roles.items():
            assert not role["Properties"].get("ManagedPolicyArns")
            for policy in role["Properties"].get("Policies", []):
                for statement in policy["PolicyDocument"]["Statement"]:
                    assert statement.get("Resource") != "*", (role_name, statement)
                    actions = statement.get("Action") if isinstance(statement.get("Action"), list) else [statement.get("Action")]
                    assert "*" not in actions, (role_name, actions)
                    statements.append(statement)
                    # Ingestion roles must NEVER possess sqs:SendMessage in F1 outbox design
                    if role_name in ("OrderServiceRole", "ExperimentServiceRole"):
                        assert not any(a.startswith("sqs:") for a in actions), (role_name, actions)

        definition = resources["HttpApi"]["Properties"]["Body"]
        assert definition["components"]["securitySchemes"]["AWS_IAM"]["x-amazon-apigateway-authtype"] == "awsSigv4"

        def inspect_security(value):
            if isinstance(value, dict):
                if "x-amazon-apigateway-integration" in value:
                    assert value.get("security") == [{"AWS_IAM": []}]
                for child in value.values():
                    inspect_security(child)
            elif isinstance(value, list):
                for child in value:
                    inspect_security(child)

        inspect_security(definition["paths"])
        assert resources["ExperimentOrderServiceFunction"]["Condition"] == "ExperimentsEnabled"
        assert resources["OrderServiceFunction"]["Properties"]["Environment"]["Variables"]["ENABLE_BENCHMARK_FAULT_ADAPTER"] == "false"
        assert resources["FulfillmentWorkerFunction"]["Properties"]["Role"] == {"Fn::GetAtt": ["FulfillmentWorkerRole", "Arn"]}
        assert resources["OutboxRelayFunction"]["Properties"]["Role"] == {"Fn::GetAtt": ["OutboxRelayRole", "Arn"]}
        assert resources["OutboxRecoverySweeperFunction"]["Properties"]["Role"] == {"Fn::GetAtt": ["OutboxRecoverySweeperRole", "Arn"]}

        worker_statements = roles["FulfillmentWorkerRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        for statement in worker_statements:
            actions = statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
            if any(action.startswith("sqs:") for action in actions):
                assert statement["Resource"] == [{"Fn::GetAtt": ["OrderFulfillmentQueue", "Arn"]}]

        for pub_role in ("OutboxRelayRole", "OutboxRecoverySweeperRole"):
            pub_stmts = roles[pub_role]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
            for statement in pub_stmts:
                actions = statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
                if any(action.startswith("sqs:") for action in actions):
                    assert actions == ["sqs:SendMessage"]
                    assert statement["Resource"] == [{"Fn::GetAtt": ["OrderFulfillmentQueue", "Arn"]}]

        def resolve_experiment(value):
            if isinstance(value, dict):
                if "Fn::If" in value and value["Fn::If"][0] == "ExperimentsEnabled":
                    return resolve_experiment(value["Fn::If"][1 if enabled == "true" else 2])
                return {k: resolve_experiment(v) for k, v in value.items()}
            if isinstance(value, list):
                return [resolve_experiment(v) for v in value]
            return value

        experiment_path = resolve_experiment(definition["paths"]["/experiments/orders"])
        if enabled == "false":
            assert experiment_path == {"Ref": "AWS::NoValue"}
        else:
            assert experiment_path["post"]["security"] == [{"AWS_IAM": []}]

        (state / f"transformed-{enabled}.json").write_text(json.dumps(translated, indent=2), encoding="utf-8")
        outputs.append(
            {
                "fault_toggle": enabled,
                "resources": len(resources),
                "roles": len(roles),
                "inline_statements": len(statements),
                "wildcard_resource_statements": 0,
                "managed_policies": 0,
                "iam_routes_verified": True,
                "ingestion_zero_sqs_verified": True,
            }
        )
    print(json.dumps(outputs, indent=2))
else:
    raise ValueError("Unknown local tool")
