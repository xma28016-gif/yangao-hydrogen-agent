import { expect, it } from 'vitest';
import { isLocalHost } from './local-boundary';
it('本地入口只允许约定的Host，不接受DNS重绑定或其他服务端口', () => {
  expect(isLocalHost('127.0.0.1:13000')).toBe(true);
  expect(isLocalHost('localhost:13000')).toBe(true);
  for (const host of ['evil.example:13000', '127.0.0.1.evil.example:13000', '127.0.0.1:18080', null]) {
    expect(isLocalHost(host)).toBe(false);
  }
});
