import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";

const source = await readFile(new URL("../app/lib/resourceCache.ts", import.meta.url), "utf8");
const cache = await import(`data:text/javascript;base64,${Buffer.from(stripTypeScriptTypes(source)).toString("base64")}`);

test("read cache is browser-only and does not share SSR account data", async () => {
  delete globalThis.window;
  let calls = 0;
  await cache.fetchResource("/me/teams", async () => ++calls);
  await cache.fetchResource("/me/teams", async () => ++calls);
  assert.equal(calls, 2);
  assert.equal(cache.peekResource("/me/teams"), null);
});

test("concurrent reads coalesce and fresh remounts reuse data", async () => {
  globalThis.window = {};
  cache.clearResourceCache();
  let calls = 0;
  const load = async () => { calls++; return { name: "fixture" }; };
  const [a, b] = await Promise.all([cache.fetchResource("/me/teams", load), cache.fetchResource("/me/teams", load)]);
  assert.deepEqual(a, b);
  assert.equal(calls, 1);
  await cache.fetchResource("/me/teams", load);
  assert.equal(calls, 1);
  await cache.fetchResource("/me/teams", load, true);
  assert.equal(calls, 2);
});

test("invalidation prevents an old in-flight account response restoring cache", async () => {
  globalThis.window = {};
  cache.clearResourceCache();
  let finish;
  const request = cache.fetchResource("/me/teams", () => new Promise((resolve) => { finish = resolve; }));
  await Promise.resolve();
  cache.clearResourceCache();
  finish({ owner: "old-account" });
  await request;
  assert.equal(cache.peekResource("/me/teams"), null);
  const current = await cache.fetchResource("/me/teams", async () => ({ owner: "current-account" }));
  assert.equal(current.owner, "current-account");
});

test("TTL revalidates and stale data expires", async () => {
  globalThis.window = {};
  cache.clearResourceCache();
  const realNow = Date.now;
  let now = 100_000, calls = 0;
  Date.now = () => now;
  try {
    await cache.fetchResource("/problems/example", async () => ++calls);
    now += 16_000;
    assert.equal(cache.peekResource("/problems/example"), 1);
    await cache.fetchResource("/problems/example", async () => ++calls);
    assert.equal(calls, 2);
    now += 61_000;
    assert.equal(cache.peekResource("/problems/example"), null);
  } finally { Date.now = realNow; }
});

test("live budgets, evaluation records and admin resources bypass cache", async () => {
  globalThis.window = {};
  for (const path of ["/problems/id/evaluation-budget?team_id=x", "/problems/id/evaluation-runs?team_id=x", "/ai/keys", "/admin/users", "/compute/overview", "/works", "/works/private-id"]) {
    assert.equal(cache.isCacheableResource(path), false);
    let calls = 0;
    await cache.fetchResource(path, async () => ++calls);
    await cache.fetchResource(path, async () => ++calls);
    assert.equal(calls, 2);
  }
});

test("failed reads are retriable and never become successful cached data", async () => {
  globalThis.window = {};
  cache.clearResourceCache();
  await assert.rejects(cache.fetchResource("/content", async () => { throw Error("fixture outage"); }));
  assert.equal(cache.peekResource("/content"), null);
  assert.deepEqual(await cache.fetchResource("/content", async () => []), []);
});
