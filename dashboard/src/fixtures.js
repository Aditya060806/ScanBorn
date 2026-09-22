/**
 * The offline fixture: a real orchestrator run, replayed.
 *
 * fixtures.json is written by `python scripts/record_dashboard_fixture.py`, which drives every
 * stage of orchestrator/service.py in-process on the synthetic test room and saves the raw
 * responses. Replaying them through pipeline.js — the same mappers live calls use — is what
 * keeps the fixture from showing a field or a value the API would not return. Re-record it
 * whenever a response shape changes.
 */
import recorded from "./fixtures.json";
import { EMPTY_PIPE, STAGE_ORDER, toPipe } from "./pipeline.js";

export const FIXTURE_SOURCE = recorded.source;
export const FIXTURE_RECORDED_AT = recorded.recorded_at;
export const FIXTURE_VISUALIZATION = recorded.responses.visualization;
export const FIXTURE_ORDER = Object.freeze([...STAGE_ORDER, "sync"]);

const start = () => ({ ...EMPTY_PIPE, fixture: true });
const apply = (pipe, stage) => ({ ...pipe, ...toPipe[stage](recorded.responses[stage]) });

/** The whole replayed run at once. */
export const fixturePipe = () => FIXTURE_ORDER.reduce(apply, start());

/** Replay stage by stage, calling onStage(pipe, stage) after each. */
export async function replayFixture(onStage, { stepMs = 160 } = {}) {
  let pipe = start();
  for (const stage of FIXTURE_ORDER) {
    pipe = apply(pipe, stage);
    onStage(pipe, stage);
    if (stepMs > 0) await new Promise((resolve) => setTimeout(resolve, stepMs));
  }
  return pipe;
}
