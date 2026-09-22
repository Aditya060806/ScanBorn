import { expect, it } from "vitest";
import { squareDraft, squareReport } from "./square.js";

it("constructs a rotated metre square and reports measured trace closure", () => {
  const draft = squareDraft([2,3,Math.PI/2]);
  expect(draft.via[0][0]).toBeCloseTo(2); expect(draft.via[0][1]).toBeCloseTo(4);
  expect(draft.via[2][0]).toBeCloseTo(1); expect(draft.via[2][1]).toBeCloseTo(3);
  const report = squareReport({ ...draft, length_m:4 }, [[2,3],...draft.via,[2.03,3.04]], [{th:0}]);
  expect(report.closure_m).toBeCloseTo(.05);
  expect(report.final_heading_change_deg).toBeCloseTo(-90);
  expect(report.detoured).toBe(false);
});
it("refuses non-square missions and labels planner detours", () => {
  const draft = squareDraft([0,0]);
  expect(squareReport({...draft,goal:[2,2]}, [[0,0],[2,2]])).toBeNull();
  expect(squareReport({...draft,length_m:5}, [[0,0],[1,0]])).toMatchObject({detoured:true});
  expect(squareReport({...draft,length_m:4}, [])).toBeNull();
  expect(() => squareDraft([NaN,0])).toThrow();
});
