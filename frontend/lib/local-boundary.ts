export function isLocalHost(host: string | null): boolean {
  return host === '127.0.0.1:13000' || host === 'localhost:13000';
}
