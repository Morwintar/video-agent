const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { MessageChannel } = require("node:worker_threads");

const workerPath = path.join(__dirname, "../dashboard/media-service-worker.js");
const script = fs.readFileSync(workerPath, "utf8");
const handlers = new Map();
const knownClients = new Set(["iframe-client"]);
let authorizeClient = false;
let forwardedRequest = null;
const selfStub = {
  location: new URL("https://dashboard.test/hermes/dashboard-plugins/waiting-studio/media-service-worker.js"),
  registration: { scope: "https://dashboard.test/hermes/dashboard-plugins/waiting-studio/" },
  clients: {
    async get(clientId) {
      return knownClients.has(clientId) ? {
        id: clientId,
        postMessage(message, ports) {
          if (!authorizeClient || message.type !== "waiting-studio:request-media-auth" || !ports || !ports[0]) return;
          ports[0].postMessage({
            type: "waiting-studio:media-auth-response",
            token: "refreshed-test-token",
            filename: message.filename,
            apiUrl: `https://dashboard.test/hermes/api/plugins/waiting-studio/videos/waiting/${encodeURIComponent(message.filename)}?profile=work&codec=auto`,
          });
        },
      } : null;
    },
    async claim() {},
  },
  addEventListener(name, handler) { handlers.set(name, handler); },
  skipWaiting: async () => {},
};

vm.runInNewContext(script, {
  self: selfStub,
  URL,
  Request,
  Headers,
  Response,
  fetch: async (request) => {
    forwardedRequest = request;
    return new Response("bytes-5-9", { status: 206, headers: { "Content-Range": "bytes 5-9/100" } });
  },
  MessageChannel,
  setTimeout,
  clearTimeout,
});

async function main() {
  assert.equal(typeof handlers.get("message"), "function", "worker should accept per-client media authorization messages");
  assert.equal(typeof handlers.get("fetch"), "function", "worker should intercept its scoped media URLs");

  handlers.get("message")({
    data: {
      type: "waiting-studio:set-media-auth",
      token: "ephemeral-test-token",
      filename: "records/waiting-vod.mp4",
      apiUrl: "https://dashboard.test/hermes/api/plugins/waiting-studio/videos/waiting/records%2Fwaiting-vod.mp4?profile=work&codec=auto",
    },
    source: { id: "iframe-client" },
  });

  let responsePromise = null;
  const request = new Request("https://dashboard.test/hermes/dashboard-plugins/waiting-studio/stream/records%2Fwaiting-vod.mp4", {
    headers: { Range: "bytes=5-9" },
  });
  handlers.get("fetch")({
    request,
    clientId: "iframe-client",
    respondWith(promise) { responsePromise = promise; },
  });
  assert.ok(responsePromise, "the worker should proxy a media URL from its controlled player iframe");
  const response = await responsePromise;
  assert.equal(response.status, 206);
  assert.equal(response.headers.get("content-range"), "bytes 5-9/100");
  assert.equal(await response.text(), "bytes-5-9");
  assert.ok(forwardedRequest instanceof Request);
  assert.equal(forwardedRequest.url, "https://dashboard.test/hermes/api/plugins/waiting-studio/videos/waiting/records%2Fwaiting-vod.mp4?profile=work&codec=auto");
  assert.equal(forwardedRequest.headers.get("range"), "bytes=5-9", "the byte range must survive the proxy");
  assert.equal(forwardedRequest.mode, "same-origin", "media authorization must never be sent cross-origin");
  assert.equal(forwardedRequest.headers.get("x-hermes-session-token"), "ephemeral-test-token");
  assert.equal(new URL(forwardedRequest.url).searchParams.has("token"), false, "never place the session token in the URL");

  let outsideScopeIntercepted = false;
  handlers.get("fetch")({
    request: new Request("https://dashboard.test/api/plugins/waiting-studio/content/records%2Fwaiting-vod.mp4"),
    clientId: "iframe-client",
    respondWith() { outsideScopeIntercepted = true; },
  });
  assert.equal(outsideScopeIntercepted, false, "the worker must leave unrelated dashboard requests untouched");

  handlers.get("message")({ data: { type: "waiting-studio:clear-media-auth" }, source: { id: "iframe-client" } });
  authorizeClient = true;
  responsePromise = null;
  handlers.get("fetch")({
    request,
    clientId: "iframe-client",
    respondWith(promise) { responsePromise = promise; },
  });
  assert.equal((await responsePromise).status, 206, "a restarted worker should ask the live iframe for an ephemeral session token");
  assert.equal(forwardedRequest.headers.get("x-hermes-session-token"), "refreshed-test-token");
  assert.equal(new URL(forwardedRequest.url).searchParams.has("token"), false);

  handlers.get("message")({ data: { type: "waiting-studio:clear-media-auth" }, source: { id: "iframe-client" } });
  knownClients.delete("iframe-client");
  responsePromise = null;
  handlers.get("fetch")({
    request,
    clientId: "iframe-client",
    respondWith(promise) { responsePromise = promise; },
  });
  assert.equal((await responsePromise).status, 401, "a closed player iframe must not authorize any further proxy reads");
}

main().then(() => {
  console.log("Waiting media service worker tests passed.");
}).catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
