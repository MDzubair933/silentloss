import copy
import re
import threading
import uuid
from typing import Any, Dict, List, Optional
from botocore.exceptions import ClientError, ReadTimeoutError


class StatefulAwsMock:
    """
    Thread-safe stateful in-memory test double for Amazon DynamoDB (tables, GSIs,
    streams capture, conditional transactions, conditional updates) and Amazon SQS.
    Enforces ConditionExpressions, ConsistentRead, pagination, and fault simulation.
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.tables: Dict[str, Dict[tuple, Dict[str, Any]]] = {}
        self.queues: Dict[str, List[Dict[str, Any]]] = {}
        self.stream_events: List[Dict[str, Any]] = []
        self._seq_counter = 0
        self.fail_next_ddb_transact_timeout: int = 0
        self.fail_next_ddb_transact_commit_then_timeout: int = 0
        self.fail_next_sqs_timeout: int = 0
        self.fail_next_sqs_503: int = 0
        self.sqs_receive_called: bool = False

    def ensure_table(self, table_name: str, key_attrs: List[str]):
        with self.lock:
            if table_name not in self.tables:
                self.tables[table_name] = {}

    def _key_tuple(self, table_name: str, key: Dict[str, Any]) -> tuple:
        if "fulfillment_id" in key:
            return (key["order_id"]["S"], key["fulfillment_id"]["S"])
        if "outbox_id" in key:
            return (key["outbox_id"]["S"],)
        if "idempotency_key" in key:
            return (key["idempotency_key"]["S"],)
        if "rejection_id" in key:
            return (key["rejection_id"]["S"],)
        return (key["order_id"]["S"],)

    def _item_key_tuple(self, table_name: str, item: Dict[str, Any]) -> tuple:
        if "fulfillment_id" in item and "order_id" in item:
            return (item["order_id"]["S"], item["fulfillment_id"]["S"])
        if "outbox_id" in item and "dispatch_state" in item:
            return (item["outbox_id"]["S"],)
        if "idempotency_key" in item and "customer_id" not in item:
            return (item["idempotency_key"]["S"],)
        if "rejection_id" in item:
            return (item["rejection_id"]["S"],)
        return (item["order_id"]["S"],)

    def _attr_val(self, item: Optional[Dict[str, Any]], attr: str):
        if not item or attr not in item:
            return None
        v = item[attr]
        if "S" in v:
            return v["S"]
        if "N" in v:
            return float(v["N"])
        return v

    def _resolve_val(self, expr_vals: Dict[str, Any], token: str):
        v = expr_vals[token]
        if "S" in v:
            return v["S"]
        if "N" in v:
            return float(v["N"])
        return v

    def _eval_condition(
        self,
        item: Optional[Dict[str, Any]],
        cond: Optional[str],
        expr_vals: Optional[Dict[str, Any]] = None,
        expr_names: Optional[Dict[str, str]] = None,
    ) -> bool:
        if not cond:
            return True
        vals = expr_vals or {}
        names = expr_names or {}
        clauses = [c.strip() for c in cond.split(" AND ")]
        for cl in clauses:
            if cl.startswith("(") and cl.endswith(")") and " OR " in cl:
                inner = cl[1:-1]
                sub_parts = [p.strip() for p in inner.split(" OR ")]
                if not any(self._eval_condition(item, p.strip("()"), vals, names) for p in sub_parts):
                    return False
                continue

            if " OR " in cl:
                sub_parts = [p.strip() for p in cl.split(" OR ")]
                if not any(self._eval_condition(item, p.strip("()"), vals, names) for p in sub_parts):
                    return False
                continue

            m_not_exists = re.fullmatch(r"attribute_not_exists\(([^)]+)\)", cl)
            if m_not_exists:
                if item is not None and m_not_exists.group(1) in item:
                    return False
                continue

            m_exists = re.fullmatch(r"attribute_exists\(([^)]+)\)", cl)
            if m_exists:
                if item is None or m_exists.group(1) not in item:
                    return False
                continue

            m_in = re.fullmatch(r"([#A-Za-z0-9_]+)\s+IN\s*\(([^)]+)\)", cl)
            if m_in:
                raw_attr = m_in.group(1)
                attr = names.get(raw_attr, raw_attr)
                choices = [self._resolve_val(vals, t.strip()) for t in m_in.group(2).split(",")]
                if self._attr_val(item, attr) not in choices:
                    return False
                continue

            m_cmp = re.fullmatch(r"([#A-Za-z0-9_]+)\s*(=|<>|<=|>=|<|>)\s*(:[A-Za-z0-9_]+)", cl)
            if m_cmp:
                raw_attr, op, val_tok = m_cmp.group(1), m_cmp.group(2), m_cmp.group(3)
                attr = names.get(raw_attr, raw_attr)
                lhs = self._attr_val(item, attr)
                rhs = self._resolve_val(vals, val_tok)
                if op == "=" and lhs != rhs:
                    return False
                if op == "<>" and lhs == rhs:
                    return False
                if op == "<=" and (lhs is None or lhs > rhs):
                    return False
                if op == ">=" and (lhs is None or lhs < rhs):
                    return False
                if op == "<" and (lhs is None or lhs >= rhs):
                    return False
                if op == ">" and (lhs is None or lhs <= rhs):
                    return False
                continue

        return True

    def _apply_update(
        self,
        item: Dict[str, Any],
        update_expr: str,
        expr_vals: Dict[str, Any],
        expr_names: Optional[Dict[str, str]] = None,
    ) -> None:
        names = expr_names or {}
        # Handle optional trailing ADD clause
        set_part = update_expr
        add_part = ""
        if " ADD " in update_expr:
            set_part, add_part = update_expr.split(" ADD ", 1)
        if set_part.startswith("SET "):
            set_body = set_part[4:]
            for assign in [s.strip() for s in set_body.split(",") if s.strip()]:
                m_arith = re.fullmatch(r"([#A-Za-z0-9_]+)\s*=\s*([#A-Za-z0-9_]+)\s*([+-])\s*(:[A-Za-z0-9_]+)", assign)
                if m_arith:
                    target_raw, src_raw, op, tok = m_arith.groups()
                    target = names.get(target_raw, target_raw)
                    src = names.get(src_raw, src_raw)
                    cur_val = int(float(item.get(src, {}).get("N", 0)))
                    delta = int(float(expr_vals[tok]["N"]))
                    new_val = cur_val + delta if op == "+" else cur_val - delta
                    item[target] = {"N": str(new_val)}
                    continue
                m_eq = re.fullmatch(r"([#A-Za-z0-9_]+)\s*=\s*(:[A-Za-z0-9_]+)", assign)
                if m_eq:
                    target_raw, tok = m_eq.groups()
                    target = names.get(target_raw, target_raw)
                    item[target] = copy.deepcopy(expr_vals[tok])
        if add_part.strip():
            for add_clause in [a.strip() for a in add_part.split(",") if a.strip()]:
                m_add = re.fullmatch(r"([#A-Za-z0-9_]+)\s+(:[A-Za-z0-9_]+)", add_clause)
                if m_add:
                    target_raw, tok = m_add.groups()
                    target = names.get(target_raw, target_raw)
                    cur = int(float(item.get(target, {}).get("N", 0)))
                    delta = int(float(expr_vals[tok]["N"]))
                    item[target] = {"N": str(cur + delta)}

    def _maybe_record_outbox_stream(self, table_name: str, item: Dict[str, Any]):
        if "outbox" in table_name.lower() and "outbox_id" in item:
            self._seq_counter += 1
            self.stream_events.append(
                {
                    "eventID": f"evt_seq_{self._seq_counter}",
                    "eventName": "INSERT",
                    "dynamodb": {
                        "SequenceNumber": str(self._seq_counter),
                        "Keys": {"outbox_id": {"S": item["outbox_id"]["S"]}},
                        "NewImage": copy.deepcopy(item),
                    },
                }
            )

    def drain_outbox_stream_events(self) -> List[Dict[str, Any]]:
        with self.lock:
            evts = list(self.stream_events)
            self.stream_events.clear()
            return evts

    def get_item(self, TableName: str, Key: Dict[str, Any], ConsistentRead: bool = False) -> Dict[str, Any]:
        with self.lock:
            tbl = self.tables.setdefault(TableName, {})
            kt = self._key_tuple(TableName, Key)
            if kt in tbl:
                return {"Item": copy.deepcopy(tbl[kt])}
            return {}

    def put_item(
        self,
        TableName: str,
        Item: Dict[str, Any],
        ConditionExpression: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self.lock:
            tbl = self.tables.setdefault(TableName, {})
            kt = self._item_key_tuple(TableName, Item)
            existing = tbl.get(kt)
            if ConditionExpression and "attribute_not_exists" in ConditionExpression and existing is not None:
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException", "Message": "Condition failed"}},
                    "PutItem",
                )
            tbl[kt] = copy.deepcopy(Item)
            if existing is None:
                self._maybe_record_outbox_stream(TableName, Item)
            return {}

    def update_item(
        self,
        TableName: str,
        Key: Dict[str, Any],
        UpdateExpression: str,
        ExpressionAttributeValues: Dict[str, Any],
        ExpressionAttributeNames: Optional[Dict[str, str]] = None,
        ConditionExpression: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self.lock:
            tbl = self.tables.setdefault(TableName, {})
            kt = self._key_tuple(TableName, Key)
            existing = tbl.get(kt)
            if ConditionExpression and not self._eval_condition(existing, ConditionExpression, ExpressionAttributeValues, ExpressionAttributeNames):
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException", "Message": "Condition failed"}},
                    "UpdateItem",
                )
            item = tbl.setdefault(kt, copy.deepcopy(Key))
            self._apply_update(item, UpdateExpression, ExpressionAttributeValues, ExpressionAttributeNames)
            return {}

    def query(
        self,
        TableName: str,
        KeyConditionExpression: str,
        ExpressionAttributeValues: Dict[str, Any],
        ConsistentRead: bool = False,
        IndexName: Optional[str] = None,
        ExclusiveStartKey: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self.lock:
            tbl = self.tables.setdefault(TableName, {})
            if IndexName == "DispatchStateIndex" or ":st" in ExpressionAttributeValues:
                target_state = ExpressionAttributeValues[":st"]["S"]
                max_next = int(float(ExpressionAttributeValues[":now"]["N"])) if ":now" in ExpressionAttributeValues else None
                matches = []
                for v in tbl.values():
                    if v.get("dispatch_state", {}).get("S") != target_state:
                        continue
                    if max_next is not None and int(float(v.get("next_attempt_at", {}).get("N", 0))) > max_next:
                        continue
                    matches.append(copy.deepcopy(v))
                return {"Items": matches}

            oid = ExpressionAttributeValues[":oid"]["S"]
            matches = [copy.deepcopy(v) for v in tbl.values() if v.get("order_id", {}).get("S") == oid]
            return {"Items": matches}

    def scan(
        self,
        TableName: str,
        ConsistentRead: bool = False,
        FilterExpression: Optional[str] = None,
        ExpressionAttributeValues: Optional[Dict[str, Any]] = None,
        ExclusiveStartKey: Optional[Dict[str, Any]] = None,
        Limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        with self.lock:
            tbl = self.tables.setdefault(TableName, {})
            all_items = [copy.deepcopy(v) for v in tbl.values()]
            if FilterExpression and ExpressionAttributeValues and ":prefix" in ExpressionAttributeValues:
                prefix = ExpressionAttributeValues[":prefix"]["S"]
                all_items = [i for i in all_items if i.get("order_id", {}).get("S", "").startswith(prefix)]

            start_idx = 0
            if ExclusiveStartKey and "_idx" in ExclusiveStartKey:
                start_idx = int(ExclusiveStartKey["_idx"]["N"])

            page_size = Limit or 2  # Small page size exercises pagination loops!
            page = all_items[start_idx : start_idx + page_size]
            res: Dict[str, Any] = {"Items": page}
            if start_idx + page_size < len(all_items):
                res["LastEvaluatedKey"] = {"_idx": {"N": str(start_idx + page_size)}}
            return res

    def transact_write_items(self, TransactItems: List[Dict[str, Any]]) -> Dict[str, Any]:
        with self.lock:
            if self.fail_next_ddb_transact_timeout > 0:
                self.fail_next_ddb_transact_timeout -= 1
                raise ReadTimeoutError(endpoint_url="https://dynamodb.us-east-1.amazonaws.com")

            reasons = []
            any_cancel = False
            for entry in TransactItems:
                if "Put" in entry:
                    op = entry["Put"]
                    tbl = self.tables.setdefault(op["TableName"], {})
                    kt = self._item_key_tuple(op["TableName"], op["Item"])
                    existing = tbl.get(kt)
                    if not self._eval_condition(existing, op.get("ConditionExpression"), op.get("ExpressionAttributeValues"), op.get("ExpressionAttributeNames")):
                        reasons.append({"Code": "ConditionalCheckFailed"})
                        any_cancel = True
                    else:
                        reasons.append({"Code": "None"})
                elif "Update" in entry:
                    op = entry["Update"]
                    tbl = self.tables.setdefault(op["TableName"], {})
                    kt = self._key_tuple(op["TableName"], op["Key"])
                    item = tbl.get(kt)
                    if not self._eval_condition(item, op.get("ConditionExpression"), op.get("ExpressionAttributeValues"), op.get("ExpressionAttributeNames")):
                        reasons.append({"Code": "ConditionalCheckFailed"})
                        any_cancel = True
                    else:
                        reasons.append({"Code": "None"})

            if any_cancel:
                raise ClientError(
                    {
                        "Error": {"Code": "TransactionCanceledException", "Message": "Transaction cancelled"},
                        "CancellationReasons": reasons,
                    },
                    "TransactWriteItems",
                )

            for entry in TransactItems:
                if "Put" in entry:
                    op = entry["Put"]
                    tbl = self.tables.setdefault(op["TableName"], {})
                    kt = self._item_key_tuple(op["TableName"], op["Item"])
                    existed = kt in tbl
                    tbl[kt] = copy.deepcopy(op["Item"])
                    if not existed:
                        self._maybe_record_outbox_stream(op["TableName"], op["Item"])
                elif "Update" in entry:
                    op = entry["Update"]
                    self.update_item(
                        TableName=op["TableName"],
                        Key=op["Key"],
                        UpdateExpression=op["UpdateExpression"],
                        ExpressionAttributeValues=op["ExpressionAttributeValues"],
                        ExpressionAttributeNames=op.get("ExpressionAttributeNames"),
                    )

            if self.fail_next_ddb_transact_commit_then_timeout > 0:
                self.fail_next_ddb_transact_commit_then_timeout -= 1
                raise ReadTimeoutError(endpoint_url="https://dynamodb.us-east-1.amazonaws.com")

            return {}

    def send_message(self, QueueUrl: str, MessageBody: str, MessageAttributes: Optional[Dict] = None) -> Dict[str, Any]:
        with self.lock:
            if self.fail_next_sqs_timeout > 0:
                self.fail_next_sqs_timeout -= 1
                raise ReadTimeoutError(endpoint_url=QueueUrl)
            if self.fail_next_sqs_503 > 0:
                self.fail_next_sqs_503 -= 1
                raise ClientError(
                    {"Error": {"Code": "ServiceUnavailable", "Message": "Service Unavailable"}},
                    "SendMessage",
                )
            q = self.queues.setdefault(QueueUrl, [])
            msg_id = f"msg_{uuid.uuid4().hex[:12]}"
            q.append({"messageId": msg_id, "body": MessageBody, "messageAttributes": MessageAttributes or {}})
            return {"MessageId": msg_id}

    def get_queue_attributes(self, QueueUrl: str, AttributeNames: List[str]) -> Dict[str, Any]:
        with self.lock:
            q = self.queues.get(QueueUrl, [])
            return {
                "Attributes": {
                    "ApproximateNumberOfMessages": str(len(q)),
                    "ApproximateNumberOfMessagesNotVisible": "0",
                }
            }

    def receive_message(self, *args, **kwargs):
        self.sqs_receive_called = True
        raise AssertionError("Reconciliation collector must NEVER consume active SQS messages!")
