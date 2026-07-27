const DEV_USER_KEY = "eskd_dev_user";
const DEV_ROLES_KEY = "eskd_dev_roles";

export function getDevUser(): string {
  return localStorage.getItem(DEV_USER_KEY) || "otk.ivanov";
}

export function getDevRoles(): string {
  return localStorage.getItem(DEV_ROLES_KEY) || "ESKD_OTK";
}

export function setDevAuth(user: string, roles: string): void {
  localStorage.setItem(DEV_USER_KEY, user);
  localStorage.setItem(DEV_ROLES_KEY, roles);
}

export async function apiGet<T>(path: string): Promise<T> {
  const resp = await fetch(path, {
    headers: {
      "X-Dev-User": getDevUser(),
      "X-Dev-Roles": getDevRoles(),
    },
  });
  if (!resp.ok) {
    throw new Error(await resp.text());
  }
  return resp.json() as Promise<T>;
}
