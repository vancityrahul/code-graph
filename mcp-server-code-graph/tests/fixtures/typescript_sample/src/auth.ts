import { createHash } from "crypto";

export class AuthManager {
  private secret: string;

  constructor(secret: string) {
    this.secret = secret;
  }

  hashPassword(password: string): string {
    return createHash("sha256").update(password).digest("hex");
  }

  async login(username: string, password: string): Promise<string | null> {
    if (!username || !password) return null;
    return `token_${username}`;
  }
}

export function createAuthManager(secret: string): AuthManager {
  return new AuthManager(secret);
}
