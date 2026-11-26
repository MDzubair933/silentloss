import { describe, expect, it } from 'vitest';
import {
  beforeAfterDataset,
  claimsMatrix,
  finalBenchmarkSummary,
  recoveryTraces,
} from '../benchmarkData';
import { buildSimulationSteps, type SimArchitecture, type SimScenario } from '../simulatorEngine';
import { WA_PILLAR_SCORECARD, WA_REVIEW_OVERVIEW } from '../wellArchitectedData';

const LEAK_PATTERNS = [
  /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/,
  /[a-z0-9]{10}\.execute-api\.[a-z0-9-]+\.amazonaws\.com/i,
  /\d{3}\*\*\*\d{4}/,
  /arn:aws:/i,
  /[A-Z]:\\\\AWS/i,
];

const sourceTextFiles = import.meta.glob<string>(
  ['/src/**/*.{ts,tsx,css,json}', '!/src/**/__tests__/**'],
  { query: '?raw', import: 'default', eager: true }
);

describe('SilentLoss static benchmark datasets', () => {
  it('enforces C = F + P + E + U + I invariant on Before and Corrected After campaigns', () => {
    const before = finalBenchmarkSummary.before_campaign.combined;
    const after = finalBenchmarkSummary.corrected_after_campaign.combined;

    expect(before.total_committed_orders_C).toBe(160);
    expect(before.fulfilled_orders_F).toBe(150);
    expect(before.confirmed_silent_loss_U).toBe(10);
    expect(before.silent_loss_rate_percent).toBe(6.25);
    expect(before.invariant_holds).toBe(true);
    expect(
      before.fulfilled_orders_F +
        before.durably_pending_P +
        before.explicitly_failed_E +
        before.confirmed_silent_loss_U +
        before.indeterminate_I
    ).toBe(before.total_committed_orders_C);

    expect(after.total_committed_orders_C).toBe(160);
    expect(after.fulfilled_orders_F).toBe(160);
    expect(after.confirmed_silent_loss_U).toBe(0);
    expect(after.duplicate_completed_effects_D).toBe(0);
    expect(after.silent_loss_rate_percent).toBe(0);
    expect(after.invariant_holds).toBe(true);
    expect(after.clean_success_verified).toBe(true);
    expect(
      after.fulfilled_orders_F +
        after.durably_pending_P +
        after.explicitly_failed_E +
        after.confirmed_silent_loss_U +
        after.indeterminate_I
    ).toBe(after.total_committed_orders_C);
  });

  it('includes all 10 live B-02 recovery traces with 0 duplicates and valid elapsed recovery', () => {
    expect(recoveryTraces).toHaveLength(10);
    for (const trace of recoveryTraces) {
      expect(trace.final_business_effect.fulfillment_status).toBe('COMPLETED');
      expect(trace.final_business_effect.orders_status).toBe('FULFILLED');
      expect(trace.outbox_durable_state.dispatch_state).toBe('CONFIRMED');
      expect(trace.outbox_durable_state.recovery_source).toBe('dynamodb_stream');
      expect(trace.outbox_durable_state.recovery_time_sec).toBeGreaterThanOrEqual(1.667);
      expect(trace.outbox_durable_state.recovery_time_sec).toBeLessThanOrEqual(2.8);
    }

    const sweeper = finalBenchmarkSummary.corrected_after_campaign.sweeper_live_pickup_order;
    expect(sweeper.order_id).toBe('ord_fin26r1__B-02_0009');
    expect(sweeper.trigger_source).toBe('scheduled_sweeper');
    expect(sweeper.recovery_time_sec).toBe(0.474);
  });

  it('aligns beforeAfterDataset summary, Well-Architected counts, and Claims Matrix', () => {
    expect(beforeAfterDataset.before_vs_corrected_live.C.before).toBe(160);
    expect(beforeAfterDataset.before_vs_corrected_live.C.corrected_live_after).toBe(160);
    expect(beforeAfterDataset.before_vs_corrected_live.U.before).toBe(10);
    expect(beforeAfterDataset.before_vs_corrected_live.U.corrected_live_after).toBe(0);
    expect(WA_REVIEW_OVERVIEW.bpsAssessed).toContain('63 / 63');
    expect(WA_REVIEW_OVERVIEW.questionsAssessed).toContain('13 / 13');
    expect(WA_PILLAR_SCORECARD).toHaveLength(13);
    expect(claimsMatrix.length).toBeGreaterThanOrEqual(10);
  });
});

describe('Deterministic Failure Simulator Engine', () => {
  const architectures: SimArchitecture[] = ['before', 'after'];
  const scenarios: SimScenario[] = ['healthy', 'timeout', 'transient503', 'duplicate'];

  it('produces valid traces for every architecture and scenario pair', () => {
    for (const arch of architectures) {
      for (const scenario of scenarios) {
        const steps = buildSimulationSteps(arch, scenario);
        expect(steps.length).toBeGreaterThanOrEqual(3);
        const terminal = steps[steps.length - 1];
        if (arch === 'before' && scenario === 'timeout') {
          expect(terminal.snapshot.reconciliationClass).toBe('U (Silent Loss)');
          expect(terminal.snapshot.fulfillmentCount).toBe(0);
        } else {
          expect(terminal.snapshot.reconciliationClass).toBe('F (Fulfilled)');
          expect(terminal.snapshot.fulfillmentCount).toBe(1);
        }
      }
    }
  });
});

describe('Privacy and Typography Hygiene', () => {
  it('contains zero sensitive AWS identifiers or em/en dashes across source files and bundled JSON', () => {
    const entries = Object.entries(sourceTextFiles);
    expect(entries.length).toBeGreaterThan(10);

    for (const [filePath, content] of entries) {
      for (const pattern of LEAK_PATTERNS) {
        expect(content, `Leak pattern ${pattern} in ${filePath}`).not.toMatch(pattern);
      }
      if (!filePath.endsWith('.json')) {
        expect(content, `Disallowed em-dash in ${filePath}`).not.toContain('\u2014');
        expect(content, `Disallowed en-dash in ${filePath}`).not.toContain('\u2013');
      }
    }
  });
});
