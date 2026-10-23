// The robots the Yarbo dashboard gets views for: the devices that have yarbie entities.
// The strategy and the Yard editor both count them this way, so the editor's links name
// the views the strategy made (they are named by device when there are several).

export function yarbieDevices(entities: Record<string, { platform?: string; device_id?: string | null }>): string[] {
  return [
    ...new Set(
      Object.values(entities)
        .filter((e) => e.platform === "yarbie" && e.device_id)
        .map((e) => e.device_id as string),
    ),
  ];
}
