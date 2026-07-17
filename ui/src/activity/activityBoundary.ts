interface IdentifiedActivity {
  id: string;
}

export function activitiesAfterBoundary<T extends IdentifiedActivity>(
  activities: T[],
  boundaryId: string | undefined,
): T[] {
  if (boundaryId === undefined) return activities;
  const boundaryIndex = activities.findIndex((activity) => activity.id === boundaryId);
  return boundaryIndex < 0 ? activities : activities.slice(boundaryIndex + 1);
}
