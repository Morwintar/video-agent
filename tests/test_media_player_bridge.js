const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { MessageChannel } = require("node:worker_threads");

const playerJsPath = path.join(__dirname, "../dashboard/media-player.js");
const playerHtmlPath = path.join(__dirname, "../dashboard/media-player.html");
const source = fs.readFileSync(playerJsPath, "utf8");
const html = fs.readFileSync(playerHtmlPath, "utf8");
const base = "https://dashboard.test/hermes/dashboard-plugins/@work/video-agent/";
const frameUrl = new URL("media-player.html", base).href;
const workerUrl = new URL("media-service-worker.js", base).href;
const filename = "records/waiting-vod.mp4";
const streamUrl = new URL(`stream/${encodeURIComponent(filename)}`, base).href;
const apiUrl = "https://dashboard.test/hermes/api/plugins/video-agent/videos/waiting/records%2Fwaiting-vod.mp4?profile=work&codec=auto";
const parentMessages = [];
const parentWindow = { postMessage(message, origin) { parentMessages.push({ message, origin }); } };
const windowListeners = new Map();
const serviceWorkerListeners = new Map();
const videoListeners = new Map();
const workerMessages = [];
const serviceWorkerOptions = [];
let videoLoadCount = 0;

assert.match(html, /Content-Security-Policy/);
assert.match(html, /<script src="media-player\.js" defer><\/script>/);
assert.doesNotMatch(html, /<script>\s*[^<]/i);

const activeWorker = {
  scriptURL: workerUrl,
  state: "activated",
  postMessage(message, ports) {
    workerMessages.push(message);
    if (message.type === "video-agent:set-media-auth" && ports && ports[0]) {
      ports[0].postMessage({ type: "video-agent:media-auth-ready" });
    }
  },
};
const registration = { active: activeWorker, installing: null, waiting: null };
const serviceWorkerContainer = {
  controller: activeWorker,
  async register(url, options) {
    serviceWorkerOptions.push({ url, options });
    return registration;
  },
  addEventListener(name, handler) {
    if (!serviceWorkerListeners.has(name)) serviceWorkerListeners.set(name, new Set());
    serviceWorkerListeners.get(name).add(handler);
  },
  removeEventListener(name, handler) { serviceWorkerListeners.get(name)?.delete(handler); },
};
const video = {
  currentTime: 0,
  duration: 7200,
  addEventListener(name, handler) {
    if (!videoListeners.has(name)) videoListeners.set(name, new Set());
    videoListeners.get(name).add(handler);
  },
  load() { videoLoadCount += 1; },
};
const status = { textContent: "", hidden: false };
const documentStub = {
  getElementById(id) { return id === "video-agent-video" ? video : status; },
};
const windowStub = {
  location: { origin: "https://dashboard.test", href: frameUrl },
  parent: parentWindow,
  isSecureContext: true,
  setTimeout,
  clearTimeout,
  addEventListener(name, handler) {
    if (!windowListeners.has(name)) windowListeners.set(name, new Set());
    windowListeners.get(name).add(handler);
  },
  removeEventListener(name, handler) { windowListeners.get(name)?.delete(handler); },
};

vm.runInNewContext(source, {
  window: windowStub,
  document: documentStub,
  navigator: { serviceWorker: serviceWorkerContainer },
  URL,
  MessageChannel,
  Number,
  setTimeout,
  clearTimeout,
});

async function main() {
  const [initialize] = [...windowListeners.get("message")];
  initialize({
    origin: "https://attacker.test",
    source: parentWindow,
    data: { type: "video-agent:init-media-player", token: "ignored", filename, apiUrl, streamUrl },
  });
  initialize({
    origin: windowStub.location.origin,
    source: parentWindow,
    data: {
      type: "video-agent:init-media-player",
      token: "ephemeral-test-token",
      filename,
      apiUrl: `${apiUrl}#unexpected`,
      streamUrl,
    },
  });
  await Promise.resolve();
  assert.equal(serviceWorkerOptions.length, 0, "foreign origins and fragment-bearing API URLs must not start the worker");
  parentMessages.length = 0;

  initialize({
    origin: windowStub.location.origin,
    source: parentWindow,
    data: {
      type: "video-agent:init-media-player",
      token: "ephemeral-test-token",
      filename,
      apiUrl,
      streamUrl,
    },
  });
  await new Promise((resolve) => setTimeout(resolve, 20));

  assert.equal(serviceWorkerOptions[0].url, workerUrl);
  assert.equal(serviceWorkerOptions[0].options.scope, new URL("./", frameUrl).pathname);
  assert.equal(serviceWorkerOptions[0].options.updateViaCache, "none");
  assert.equal(video.src, streamUrl, "the media element should request the scoped URL intercepted by the worker");
  assert.equal(videoLoadCount, 1);
  assert.equal(workerMessages[0].type, "video-agent:set-media-auth");
  assert.equal(workerMessages[0].token, "ephemeral-test-token");
  assert.equal(new URL(workerMessages[0].apiUrl).searchParams.has("token"), false);
  assert.equal(video.src.includes("ephemeral-test-token"), false, "the session token must not be embedded in any media URL");

  for (const handler of videoListeners.get("loadedmetadata")) handler();
  assert.ok(status.hidden, "the loading status should disappear when metadata arrives");
  assert.equal(parentMessages[0].message.type, "video-agent:player-ready");
  assert.equal(parentMessages[1].message.type, "video-agent:time-update");
  assert.equal(parentMessages[1].message.duration, 7200);
  assert.equal(parentMessages[0].origin, windowStub.location.origin);
  for (const handler of videoListeners.get("timeupdate")) handler();
  assert.equal(parentMessages[2].message.type, "video-agent:time-update");

  const seekHandler = [...windowListeners.get("message")].find((handler) => handler !== initialize);
  seekHandler({
    origin: windowStub.location.origin,
    source: parentWindow,
    data: { type: "video-agent:seek", timeSeconds: 321.5 },
  });
  assert.equal(video.currentTime, 321.5, "the iframe should support seeking to a saved marker");

  for (const handler of windowListeners.get("pagehide")) handler();
  assert.ok(workerMessages.some((message) => message.type === "video-agent:clear-media-auth"));
  console.log("Waiting media player bridge tests passed.");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
