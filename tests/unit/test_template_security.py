import yaml


def _load_template():
    class CfnLoader(yaml.SafeLoader):
        pass

    for tag in ["!Sub", "!Ref", "!GetAtt", "!If", "!Equals", "!And", "!Or", "!Not", "!Select", "!Split", "!Join"]:
        CfnLoader.add_constructor(tag, lambda loader, node: f"{tag}:{node.value}")

    with open(r"D:\AWS\hackathon2\template.yaml", "r", encoding="utf-8") as f:
        return yaml.load(f, Loader=CfnLoader)


def test_dynamodb_schema_validity_and_outbox_stream():
    tpl = _load_template()
    for name, res in tpl["Resources"].items():
        if res["Type"] == "AWS::DynamoDB::Table":
            props = res["Properties"]
            assert props["SSESpecification"]["SSEEnabled"] is True
            for attr in props["AttributeDefinitions"]:
                assert "AttributeName" in attr
                assert "AttributeType" in attr
                assert "KeyType" not in attr, f"{name} AttributeDefinitions erroneously uses KeyType"
    outbox = tpl["Resources"]["OutboxTable"]["Properties"]
    assert outbox["StreamSpecification"]["StreamViewType"] in {"NEW_IMAGE", "NEW_AND_OLD_IMAGES"}
    assert any(g["IndexName"] == "DispatchStateIndex" for g in outbox["GlobalSecondaryIndexes"])


def test_api_requires_iam_auth_and_no_public_cors_wildcard():
    tpl = _load_template()
    http_api = tpl["Resources"]["HttpApi"]["Properties"]
    for resource in tpl["Resources"].values():
        if resource["Type"] == "AWS::Serverless::Function":
            for event in resource["Properties"].get("Events", {}).values():
                if event["Type"] == "HttpApi":
                    assert event["Properties"]["Auth"]["Authorizer"] == "AWS_IAM"
    assert http_api["Auth"]["EnableIamAuthorizer"] is True
    assert "CorsConfiguration" not in http_api


def test_fault_adapter_disabled_by_default():
    tpl = _load_template()
    param = tpl["Parameters"]["EnableFaultAdapter"]
    assert param["Default"] == "false"


def test_queues_encrypted_and_concurrency_capped():
    tpl = _load_template()
    assert tpl["Resources"]["OrderFulfillmentQueue"]["Properties"]["SqsManagedSseEnabled"] is True
    assert tpl["Resources"]["FulfillmentDLQ"]["Properties"]["SqsManagedSseEnabled"] is True

    worker_props = tpl["Resources"]["FulfillmentWorkerFunction"]["Properties"]
    assert worker_props["Events"]["SQSEvent"]["Properties"]["ScalingConfig"]["MaximumConcurrency"] == 2
    for res in tpl["Resources"].values():
        if res["Type"] == "AWS::Serverless::Function":
            assert "ReservedConcurrentExecutions" not in res["Properties"]
            assert "ProvisionedConcurrencyConfig" not in res["Properties"]


def test_no_wildcard_iam_and_ingestion_has_zero_sqs_permissions():
    tpl = _load_template()
    pairs = [
        ("OrderServiceFunction", "OrderServiceRole"),
        ("OutboxRelayFunction", "OutboxRelayRole"),
        ("OutboxRecoverySweeperFunction", "OutboxRecoverySweeperRole"),
        ("FulfillmentWorkerFunction", "FulfillmentWorkerRole"),
        ("ExperimentOrderServiceFunction", "ExperimentServiceRole"),
    ]
    for fn_name, role_name in pairs:
        fn_props = tpl["Resources"][fn_name]["Properties"]
        assert fn_props["CodeUri"] == "src/lambdas/"
        assert fn_props["Handler"].startswith("handlers.")
        assert "Policies" not in fn_props
        assert "FULFILLMENT_QUEUE_URL" not in fn_props["Environment"]["Variables"] or fn_name in (
            "OutboxRelayFunction",
            "OutboxRecoverySweeperFunction",
        )
        role = tpl["Resources"][role_name]["Properties"]
        assert not role.get("ManagedPolicyArns")
        assert fn_props["Role"] == {"Fn::GetAtt": [role_name, "Arn"]}
        for pol in role["Policies"]:
            for stmt in pol["PolicyDocument"]["Statement"]:
                res_list = stmt["Resource"] if isinstance(stmt["Resource"], list) else [stmt["Resource"]]
                for r in res_list:
                    assert r != "*", f"Wildcard resource prohibited in {fn_name}"
                actions = stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
                if fn_name in ("OrderServiceFunction", "ExperimentOrderServiceFunction"):
                    assert not any(a.startswith("sqs:") for a in actions), f"{fn_name} must not have SQS permissions"
