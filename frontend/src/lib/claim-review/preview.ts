/**
 * C12 preview is a server-side, NON-PUBLIC, OFF-by-default switch.
 * The Next production-mode build is not evidence of permission to deploy.
 *
 * Requires BOTH an explicitly marked development runtime and a dedicated
 * opt-in variable. Backend C11 separately requires development + a second
 * default-OFF gate before ANY reviewer API can run.
 */
export function c12SyntheticStaffPreviewEnabled(
  env: Readonly<Record<string, string | undefined>> = process.env,
): boolean {
  return (
    env.ENVIRONMENT === "development" &&
    env.CXOPS_C12_SYNTHETIC_STAFF_PREVIEW_ENABLED === "true"
  );
}
