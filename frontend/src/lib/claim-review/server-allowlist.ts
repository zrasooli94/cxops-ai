/** Pure closed route matcher for the C12 development-only Next reviewer proxy.
 *
 * All destinations are fixed C11 paths. A browser cannot choose backend URL,
 * tenant ID, actor subject, method, arbitrary path or query string.
 */
const NUMBER = /^[1-9][0-9]{0,9}$/;
const KEY = /^[a-z][a-z0-9-]{0,79}:[a-z][a-z0-9-]{0,79}$/;
export function c12ReviewerBackendPath(
  path: readonly string[],
  method: string,
): string | null {
  if (method === "POST" && path.length === 1 && path[0] === "synthetic-batches") {
    return "/evaluations/claim-reviews/synthetic-batches";
  }
  if (
    path.length !== 3 && path.length !== 4
  ) return null;
  if (
    path[0] !== "batches" ||
    !NUMBER.test(path[1] ?? "") ||
    Number(path[1]) > 2147483647 ||
    Number(path[1]) < 1
  ) return null;
  const id = path[1];
  if (method === "POST" && path.length === 3 && path[2] === "assignments") {
    return "/evaluations/claim-reviews/batches/" + id + "/assignments";
  }
  if (method === "GET" && path.length === 3 && path[2] === "my-queue") {
    return "/evaluations/claim-reviews/batches/" + id + "/my-queue";
  }
  if (
    method === "POST" &&
    path.length === 4 &&
    KEY.test(path[3] ?? "") &&
    (path[2] === "reviews" || path[2] === "adjudications")
  ) {
    return "/evaluations/claim-reviews/batches/" + id + "/" + path[2] + "/" + path[3];
  }
  return null;
}

export function c12ReviewerAdminPath(path: readonly string[]): boolean {
  return (
    (path.length === 1 && path[0] === "synthetic-batches") ||
    (path.length === 3 && path[2] === "assignments")
  );
}
