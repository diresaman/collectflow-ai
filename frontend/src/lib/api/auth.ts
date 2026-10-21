import { backendGet, backendPost } from "./client";

export type Role = "OPERATOR" | "APPROVER" | "ADMIN";

export type CurrentUser = {
  id: number;
  username: string;
  display_name: string;
  email: string;
  role: Role;
};

export const ROLE_LABELS: Record<Role, string> = { OPERATOR: "Operator", APPROVER: "Approver", ADMIN: "Admin" };

function isCurrentUser(value: unknown): value is CurrentUser {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.id === "number" &&
    ["username", "display_name", "email"].every((key) => typeof v[key] === "string") &&
    ["OPERATOR", "APPROVER", "ADMIN"].includes(v.role as string)
  );
}

const isAnyObject = (value: unknown): value is object => typeof value === "object" && value !== null;

export function getMe(signal?: AbortSignal): Promise<CurrentUser> {
  return backendGet("/me/", isCurrentUser, { signal });
}

/** Login needs the CSRF cookie first; Django rotates it (and the session) on success. */
export async function login(username: string, password: string): Promise<CurrentUser> {
  await backendGet("/auth/csrf/", isAnyObject);
  return backendPost("/auth/login/", isCurrentUser, { body: { username, password }, timeoutMs: 10_000 });
}

export async function logout(): Promise<void> {
  await backendPost("/auth/logout/", isAnyObject);
}
