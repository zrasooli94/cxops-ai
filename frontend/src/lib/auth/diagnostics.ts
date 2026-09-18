export class NhostConfigurationError extends Error {
  constructor() {
    super("Nhost subdomain/region not configured");
    this.name = "NhostConfigurationError";
  }
}

export function structuredAuthLog(
  event: string,
  fields: Record<string, string | number | boolean>,
): void {
  console.error(JSON.stringify({ event, ...fields }));
}