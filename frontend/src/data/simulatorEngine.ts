export type SimArchitecture = "before" | "after";
export type SimScenario = "healthy" | "timeout" | "transient503" | "duplicate";

export interface SimStateSnapshot {
  httpResponse: string;
  ordersStatus: string;
  dispatchState: string;
  publicationUncertain: string;
  sendAttemptsReserved: number;
  blockedAttempts: number;
  sqsQueueCount: number;
  fulfillmentCount: number;
  reconciliationClass: "IN_FLIGHT" | "F (Fulfilled)" | "U (Silent Loss)";
}

export interface SimStep {
  id: string;
  stepNumber: number;
  elapsedLabel: string;
  title: string;
  description: string;
  activeNodeIds: string[];
  activeEdgeIds: string[];
  tone: "amber" | "emerald" | "red";
  snapshot: SimStateSnapshot;
}

const DEFAULT_SNAPSHOT: SimStateSnapshot = {
  httpResponse: "Awaiting request",
  ordersStatus: "-",
  dispatchState: "-",
  publicationUncertain: "-",
  sendAttemptsReserved: 0,
  blockedAttempts: 0,
  sqsQueueCount: 0,
  fulfillmentCount: 0,
  reconciliationClass: "IN_FLIGHT",
};

export function getInitialSnapshot(): SimStateSnapshot {
  return { ...DEFAULT_SNAPSHOT };
}

export function buildSimulationSteps(arch: SimArchitecture, scenario: SimScenario): SimStep[] {
  if (arch === "before") {
    if (scenario === "healthy") {
      return [
        {
          id: "b-h-1",
          stepNumber: 1,
          elapsedLabel: "t = 0.00s",
          title: "Client sends signed POST /orders",
          description: "API Gateway validates IAM SigV4 and routes request to OrderService Lambda.",
          activeNodeIds: ["client", "apigw", "orderFn"],
          activeEdgeIds: ["client-apigw", "apigw-order"],
          tone: "amber",
          snapshot: { ...DEFAULT_SNAPSHOT, httpResponse: "Processing" },
        },
        {
          id: "b-h-2",
          stepNumber: 2,
          elapsedLabel: "t = 0.14s",
          title: "Atomic 2-table DynamoDB commit",
          description: "OrderService commits OrdersTable (COMMITTED) + IdempotencyTable. No outbox row exists.",
          activeNodeIds: ["orderFn", "ddbCore"],
          activeEdgeIds: ["order-ddb"],
          tone: "amber",
          snapshot: {
            ...DEFAULT_SNAPSHOT,
            httpResponse: "DB Committed (pre-send)",
            ordersStatus: "COMMITTED",
            dispatchState: "NONE (Dual-Write)",
          },
        },
        {
          id: "b-h-3",
          stepNumber: 3,
          elapsedLabel: "t = 0.28s",
          title: "Synchronous SQS SendMessage succeeds",
          description: "OrderService calls SQS synchronously after the DB transaction, then returns HTTP 201.",
          activeNodeIds: ["orderFn", "sqs", "client"],
          activeEdgeIds: ["order-sqs"],
          tone: "emerald",
          snapshot: {
            ...DEFAULT_SNAPSHOT,
            httpResponse: "201 CONFIRMED / CONFIRMED",
            ordersStatus: "COMMITTED",
            dispatchState: "CONFIRMED (metadata only)",
            sendAttemptsReserved: 1,
            sqsQueueCount: 1,
          },
        },
        {
          id: "b-h-4",
          stepNumber: 4,
          elapsedLabel: "t = 0.45s",
          title: "Worker fulfills and updates order",
          description: "FulfillmentWorker consumes SQS message and commits FULFILLMENT#PRIMARY + OrdersTable FULFILLED.",
          activeNodeIds: ["sqs", "worker", "ddbFinal"],
          activeEdgeIds: ["sqs-worker", "worker-final"],
          tone: "emerald",
          snapshot: {
            ...DEFAULT_SNAPSHOT,
            httpResponse: "201 CONFIRMED / CONFIRMED",
            ordersStatus: "FULFILLED",
            dispatchState: "CONFIRMED",
            sendAttemptsReserved: 1,
            sqsQueueCount: 0,
            fulfillmentCount: 1,
            reconciliationClass: "F (Fulfilled)",
          },
        },
      ];
    }

    if (scenario === "timeout") {
      return [
        {
          id: "b-t-1",
          stepNumber: 1,
          elapsedLabel: "t = 0.00s",
          title: "Client sends signed order request",
          description: "Request arrives via API Gateway into OrderService Lambda.",
          activeNodeIds: ["client", "apigw", "orderFn"],
          activeEdgeIds: ["client-apigw", "apigw-order"],
          tone: "amber",
          snapshot: { ...DEFAULT_SNAPSHOT, httpResponse: "Processing" },
        },
        {
          id: "b-t-2",
          stepNumber: 2,
          elapsedLabel: "t = 0.14s",
          title: "Order committed durably in DynamoDB",
          description: "OrdersTable records status=COMMITTED. There is no durable outbox obligation.",
          activeNodeIds: ["orderFn", "ddbCore"],
          activeEdgeIds: ["order-ddb"],
          tone: "amber",
          snapshot: {
            ...DEFAULT_SNAPSHOT,
            httpResponse: "DB Committed (pre-send)",
            ordersStatus: "COMMITTED",
            dispatchState: "PENDING (in-memory only)",
          },
        },
        {
          id: "b-t-3",
          stepNumber: 3,
          elapsedLabel: "t = 0.65s",
          title: "Publication failure boundary hit (2 timeouts)",
          description: "Both synchronous SendMessage attempts time out (ReadTimeoutError). OrderService marks dispatch_status=UNKNOWN and returns HTTP 504.",
          activeNodeIds: ["orderFn", "boundary"],
          activeEdgeIds: ["order-boundary"],
          tone: "red",
          snapshot: {
            ...DEFAULT_SNAPSHOT,
            httpResponse: "504 QueueOutcomeUnknown",
            ordersStatus: "COMMITTED",
            dispatchState: "UNKNOWN (Stranded)",
            blockedAttempts: 2,
            sqsQueueCount: 0,
          },
        },
        {
          id: "b-t-4",
          stepNumber: 4,
          elapsedLabel: "t = 60.0s",
          title: "Silent order loss confirmed (U = 1)",
          description: "Convergence window ends. Order remains COMMITTED with zero SQS messages and zero fulfillment records. B-02 produced 25% loss in live Baseline V3.",
          activeNodeIds: ["ddbCore", "boundary"],
          activeEdgeIds: [],
          tone: "red",
          snapshot: {
            ...DEFAULT_SNAPSHOT,
            httpResponse: "504 QueueOutcomeUnknown",
            ordersStatus: "COMMITTED (No Fulfillment)",
            dispatchState: "UNKNOWN (Unrecoverable)",
            blockedAttempts: 2,
            sqsQueueCount: 0,
            fulfillmentCount: 0,
            reconciliationClass: "U (Silent Loss)",
          },
        },
      ];
    }

    if (scenario === "transient503") {
      return [
        {
          id: "b-503-1",
          stepNumber: 1,
          elapsedLabel: "t = 0.00s",
          title: "Order committed before sync SQS publish",
          description: "OrderService commits OrdersTable + IdempotencyTable first, then invokes SQS inline.",
          activeNodeIds: ["client", "apigw", "orderFn", "ddbCore"],
          activeEdgeIds: ["client-apigw", "apigw-order", "order-ddb"],
          tone: "amber",
          snapshot: {
            ...DEFAULT_SNAPSHOT,
            httpResponse: "DB Committed",
            ordersStatus: "COMMITTED",
            dispatchState: "INLINE_RETRY",
          },
        },
        {
          id: "b-503-2",
          stepNumber: 2,
          elapsedLabel: "t = 0.25s",
          title: "Attempt 1 fails with ServiceUnavailable (503)",
          description: "First inline SQS call fails with 503; bounded inline retry runs inside the same HTTP invocation.",
          activeNodeIds: ["orderFn", "boundary"],
          activeEdgeIds: ["order-boundary"],
          tone: "red",
          snapshot: {
            ...DEFAULT_SNAPSHOT,
            httpResponse: "Retrying inline (503)",
            ordersStatus: "COMMITTED",
            dispatchState: "RETRYING",
            blockedAttempts: 1,
          },
        },
        {
          id: "b-503-3",
          stepNumber: 3,
          elapsedLabel: "t = 0.48s",
          title: "Attempt 2 succeeds inline -> fulfilled",
          description: "Attempt 2 pushes SQS message before HTTP response returns 201; FulfillmentWorker completes order.",
          activeNodeIds: ["orderFn", "sqs", "worker", "ddbFinal"],
          activeEdgeIds: ["order-sqs", "sqs-worker", "worker-final"],
          tone: "emerald",
          snapshot: {
            ...DEFAULT_SNAPSHOT,
            httpResponse: "201 CONFIRMED / CONFIRMED",
            ordersStatus: "FULFILLED",
            dispatchState: "CONFIRMED",
            sendAttemptsReserved: 1,
            blockedAttempts: 1,
            sqsQueueCount: 0,
            fulfillmentCount: 1,
            reconciliationClass: "F (Fulfilled)",
          },
        },
      ];
    }

    // Before duplicate
    return [
      {
        id: "b-d-1",
        stepNumber: 1,
        elapsedLabel: "t = 0.00s",
        title: "Initial commit & fulfillment complete",
        description: "Order committed and fulfilled once (FULFILLMENT#PRIMARY).",
        activeNodeIds: ["orderFn", "ddbCore", "sqs", "worker", "ddbFinal"],
        activeEdgeIds: ["order-ddb", "order-sqs", "sqs-worker", "worker-final"],
        tone: "emerald",
        snapshot: {
          ...DEFAULT_SNAPSHOT,
          httpResponse: "201 CONFIRMED / CONFIRMED",
          ordersStatus: "FULFILLED",
          dispatchState: "CONFIRMED",
          sendAttemptsReserved: 1,
          fulfillmentCount: 1,
        },
      },
      {
        id: "b-d-2",
        stepNumber: 2,
        elapsedLabel: "t = 1.20s",
        title: "API Idempotency-Key replay & duplicate SQS event",
        description: "Client replays POST /orders (returns cached HTTP 200) and a duplicate SQS message arrives at FulfillmentWorker.",
        activeNodeIds: ["client", "apigw", "orderFn", "sqs", "worker"],
        activeEdgeIds: ["client-apigw", "apigw-order", "sqs-worker"],
        tone: "amber",
        snapshot: {
          ...DEFAULT_SNAPSHOT,
          httpResponse: "200 IsIdempotentReplay",
          ordersStatus: "FULFILLED",
          dispatchState: "CONFIRMED",
          sendAttemptsReserved: 1,
          sqsQueueCount: 1,
          fulfillmentCount: 1,
        },
      },
      {
        id: "b-d-3",
        stepNumber: 3,
        elapsedLabel: "t = 1.35s",
        title: "Worker suppresses duplicate write (D = 0)",
        description: "Conditional write attribute_not_exists(order_id) rejects duplicate side effect while cleanly deleting queue message.",
        activeNodeIds: ["worker", "ddbFinal"],
        activeEdgeIds: ["worker-final"],
        tone: "emerald",
        snapshot: {
          ...DEFAULT_SNAPSHOT,
          httpResponse: "200 IsIdempotentReplay",
          ordersStatus: "FULFILLED",
          dispatchState: "CONFIRMED",
          sendAttemptsReserved: 1,
          sqsQueueCount: 0,
          fulfillmentCount: 1,
          reconciliationClass: "F (Fulfilled)",
        },
      },
    ];
  }

  // AFTER (F1 Corrected Transactional Outbox)
  if (scenario === "healthy") {
    return [
      {
        id: "a-h-1",
        stepNumber: 1,
        elapsedLabel: "t = 0.00s",
        title: "Atomic 3-table commit -> HTTP 201 PENDING",
        description: "OrderService commits IdempotencyTable + OrdersTable (COMMITTED) + OutboxTable (PENDING) in one DynamoDB transaction, zero SQS permissions on ingestion role.",
        activeNodeIds: ["client", "apigw", "orderFn", "ddbTx"],
        activeEdgeIds: ["client-apigw", "apigw-order", "order-tx"],
        tone: "amber",
        snapshot: {
          ...DEFAULT_SNAPSHOT,
          httpResponse: "201 CONFIRMED / PENDING",
          ordersStatus: "COMMITTED",
          dispatchState: "PENDING",
          publicationUncertain: "false",
        },
      },
      {
        id: "a-h-2",
        stepNumber: 2,
        elapsedLabel: "t = 0.21s",
        title: "DynamoDB Streams triggers OutboxRelay",
        description: "OutboxRelay acquires lease, persists publication_uncertain=\"true\" + send_attempts_reserved=1 BEFORE SendMessage, publishes SQS event, and clears uncertainty atomically.",
        activeNodeIds: ["ddbTx", "streamRelay", "sqs"],
        activeEdgeIds: ["tx-stream", "relay-sqs"],
        tone: "emerald",
        snapshot: {
          ...DEFAULT_SNAPSHOT,
          httpResponse: "201 CONFIRMED / PENDING",
          ordersStatus: "COMMITTED",
          dispatchState: "CONFIRMED",
          publicationUncertain: "false",
          sendAttemptsReserved: 1,
          sqsQueueCount: 1,
        },
      },
      {
        id: "a-h-3",
        stepNumber: 3,
        elapsedLabel: "t = 0.40s",
        title: "FulfillmentWorker completes business effect",
        description: "FulfillmentWorker commits FULFILLMENT#PRIMARY + OrdersTable FULFILLED. All 3 tables converge 1:1:1.",
        activeNodeIds: ["sqs", "worker", "ddbFinal"],
        activeEdgeIds: ["sqs-worker", "worker-final"],
        tone: "emerald",
        snapshot: {
          ...DEFAULT_SNAPSHOT,
          httpResponse: "201 CONFIRMED / PENDING",
          ordersStatus: "FULFILLED",
          dispatchState: "CONFIRMED",
          publicationUncertain: "false",
          sendAttemptsReserved: 1,
          sqsQueueCount: 0,
          fulfillmentCount: 1,
          reconciliationClass: "F (Fulfilled)",
        },
      },
    ];
  }

  if (scenario === "timeout") {
    return [
      {
        id: "a-t-1",
        stepNumber: 1,
        elapsedLabel: "t = 0.00s",
        title: "Atomic commit decouples ingestion from SQS",
        description: "OrderService commits Idempotency + Orders + OutboxTable (dispatch_state=PENDING, fault_remaining_blocks=2) and immediately returns HTTP 201 PENDING.",
        activeNodeIds: ["client", "apigw", "orderFn", "ddbTx"],
        activeEdgeIds: ["client-apigw", "apigw-order", "order-tx"],
        tone: "amber",
        snapshot: {
          ...DEFAULT_SNAPSHOT,
          httpResponse: "201 CONFIRMED / PENDING",
          ordersStatus: "COMMITTED",
          dispatchState: "PENDING",
          publicationUncertain: "false",
        },
      },
      {
        id: "a-t-2",
        stepNumber: 2,
        elapsedLabel: "t = 0.18s",
        title: "OutboxRelay pass 1 hits 2 timeouts -> RETRY_WAIT",
        description: "Under lease, fault adapter decrements blocks twice (sqs_api_calls=0). Publisher preserves OutboxTable state as RETRY_WAIT (next_attempt_at=+2s) and reports batchItemFailures.",
        activeNodeIds: ["ddbTx", "streamRelay"],
        activeEdgeIds: ["tx-stream"],
        tone: "red",
        snapshot: {
          ...DEFAULT_SNAPSHOT,
          httpResponse: "201 CONFIRMED / PENDING",
          ordersStatus: "COMMITTED",
          dispatchState: "RETRY_WAIT (+2s backoff)",
          publicationUncertain: "false",
          blockedAttempts: 2,
        },
      },
      {
        id: "a-t-3",
        stepNumber: 3,
        elapsedLabel: "t = 2.05s",
        title: "Async retry reserves send & publishes SQS",
        description: "Stream ESM retry (or scheduled sweeper fallback if missed) acquires lease, writes publication_uncertain=\"true\" + send_attempts_reserved=1 pre-send, receives SQS MessageId, and atomically confirms CONFIRMED.",
        activeNodeIds: ["streamRelay", "sweeper", "sqs"],
        activeEdgeIds: ["relay-sqs"],
        tone: "emerald",
        snapshot: {
          ...DEFAULT_SNAPSHOT,
          httpResponse: "201 CONFIRMED / PENDING",
          ordersStatus: "COMMITTED",
          dispatchState: "CONFIRMED",
          publicationUncertain: "false",
          sendAttemptsReserved: 1,
          blockedAttempts: 2,
          sqsQueueCount: 1,
        },
      },
      {
        id: "a-t-4",
        stepNumber: 4,
        elapsedLabel: "t = 2.25s",
        title: "Order fulfilled cleanly (U = 0, F = 1)",
        description: "FulfillmentWorker processes SQS event idempotently. Zero silent losses across all 10 live B-02 timeout orders (1.667s-2.800s).",
        activeNodeIds: ["sqs", "worker", "ddbFinal"],
        activeEdgeIds: ["sqs-worker", "worker-final"],
        tone: "emerald",
        snapshot: {
          ...DEFAULT_SNAPSHOT,
          httpResponse: "201 CONFIRMED / PENDING",
          ordersStatus: "FULFILLED",
          dispatchState: "CONFIRMED",
          publicationUncertain: "false",
          sendAttemptsReserved: 1,
          blockedAttempts: 2,
          sqsQueueCount: 0,
          fulfillmentCount: 1,
          reconciliationClass: "F (Fulfilled)",
        },
      },
    ];
  }

  if (scenario === "transient503") {
    return [
      {
        id: "a-503-1",
        stepNumber: 1,
        elapsedLabel: "t = 0.00s",
        title: "Atomic 3-item commit returns HTTP 201 PENDING",
        description: "OrderService commits OutboxTable PENDING and exits quickly (avg 37.88 ms) without waiting on SQS retries.",
        activeNodeIds: ["client", "apigw", "orderFn", "ddbTx"],
        activeEdgeIds: ["client-apigw", "apigw-order", "order-tx"],
        tone: "amber",
        snapshot: {
          ...DEFAULT_SNAPSHOT,
          httpResponse: "201 CONFIRMED / PENDING",
          ordersStatus: "COMMITTED",
          dispatchState: "PENDING",
          publicationUncertain: "false",
        },
      },
      {
        id: "a-503-2",
        stepNumber: 2,
        elapsedLabel: "t = 0.25s",
        title: "Relay attempt 1 sees 503, attempt 2 succeeds",
        description: "OutboxRelay consumes 1 blocked attempt, reserves send_attempts_reserved=1, sends SQS event on attempt 2, and marks OutboxTable CONFIRMED (0.220s-0.570s live).",
        activeNodeIds: ["ddbTx", "streamRelay", "sqs"],
        activeEdgeIds: ["tx-stream", "relay-sqs"],
        tone: "emerald",
        snapshot: {
          ...DEFAULT_SNAPSHOT,
          httpResponse: "201 CONFIRMED / PENDING",
          ordersStatus: "COMMITTED",
          dispatchState: "CONFIRMED",
          publicationUncertain: "false",
          sendAttemptsReserved: 1,
          blockedAttempts: 1,
          sqsQueueCount: 1,
        },
      },
      {
        id: "a-503-3",
        stepNumber: 3,
        elapsedLabel: "t = 0.42s",
        title: "FulfillmentWorker commits effect",
        description: "OrdersTable reaches FULFILLED with FULFILLMENT#PRIMARY.",
        activeNodeIds: ["sqs", "worker", "ddbFinal"],
        activeEdgeIds: ["sqs-worker", "worker-final"],
        tone: "emerald",
        snapshot: {
          ...DEFAULT_SNAPSHOT,
          httpResponse: "201 CONFIRMED / PENDING",
          ordersStatus: "FULFILLED",
          dispatchState: "CONFIRMED",
          publicationUncertain: "false",
          sendAttemptsReserved: 1,
          blockedAttempts: 1,
          sqsQueueCount: 0,
          fulfillmentCount: 1,
          reconciliationClass: "F (Fulfilled)",
        },
      },
    ];
  }

  // After duplicate
  return [
    {
      id: "a-d-1",
      stepNumber: 1,
      elapsedLabel: "t = 0.00s",
      title: "Initial atomic commit, publish & fulfillment",
      description: "Order committed with deterministic UUIDv5 event_id and fulfilled once.",
      activeNodeIds: ["orderFn", "ddbTx", "streamRelay", "sqs", "worker", "ddbFinal"],
      activeEdgeIds: ["order-tx", "tx-stream", "relay-sqs", "sqs-worker", "worker-final"],
      tone: "emerald",
      snapshot: {
        ...DEFAULT_SNAPSHOT,
        httpResponse: "201 CONFIRMED / PENDING",
        ordersStatus: "FULFILLED",
        dispatchState: "CONFIRMED",
        publicationUncertain: "false",
        sendAttemptsReserved: 1,
        fulfillmentCount: 1,
      },
    },
    {
      id: "a-d-2",
      stepNumber: 2,
      elapsedLabel: "t = 0.90s",
      title: "API replay & concurrent publisher/SQS duplicate",
      description: "API replay hits IdempotencyTable returning HTTP 200 without creating a second Outbox row; lease guard & SQS duplicate delivery are exercised concurrently.",
      activeNodeIds: ["client", "orderFn", "sweeper", "sqs", "worker"],
      activeEdgeIds: ["client-apigw", "apigw-order", "sweeper-sqs", "sqs-worker"],
      tone: "amber",
      snapshot: {
        ...DEFAULT_SNAPSHOT,
        httpResponse: "200 IsIdempotentReplay",
        ordersStatus: "FULFILLED",
        dispatchState: "CONFIRMED (Lease Protected)",
        publicationUncertain: "false",
        sendAttemptsReserved: 1,
        sqsQueueCount: 1,
        fulfillmentCount: 1,
      },
    },
    {
      id: "a-d-3",
      stepNumber: 3,
      elapsedLabel: "t = 1.05s",
      title: "Idempotent suppression verified (D = 0)",
      description: "FulfillmentWorker conditional transaction ignores duplicate delivery cleanly; FulfillmentTable retains exactly 1 record per order.",
      activeNodeIds: ["worker", "ddbFinal"],
      activeEdgeIds: ["worker-final"],
      tone: "emerald",
      snapshot: {
        ...DEFAULT_SNAPSHOT,
        httpResponse: "200 IsIdempotentReplay",
        ordersStatus: "FULFILLED",
        dispatchState: "CONFIRMED",
        publicationUncertain: "false",
        sendAttemptsReserved: 1,
        sqsQueueCount: 0,
        fulfillmentCount: 1,
        reconciliationClass: "F (Fulfilled)",
      },
    },
  ];
}
