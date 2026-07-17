import { activitiesAfterBoundary } from "./activityBoundary.ts";

const activity = (id: string) => ({ id });

const retained = [activity("before"), activity("boundary"), activity("after")];
if (activitiesAfterBoundary(retained, "boundary").map(({ id }) => id).join() !== "after") {
  throw new Error("retained boundary did not exclude older activities");
}

const afterEviction = [activity("new-1"), activity("new-2")];
if (activitiesAfterBoundary(afterEviction, "evicted").length !== afterEviction.length) {
  throw new Error("evicted boundary did not treat the retained ring buffer as newer");
}

console.log("[activityBoundary.selfcheck] retained and evicted boundaries: OK");
