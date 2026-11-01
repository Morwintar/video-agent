const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const bundlePath = path.join(__dirname, "../dashboard/dist/index.js");
const stateValues = [];
let hookIndex = 0;
let registeredComponent = null;
const apiCalls = [];
const mediaCalls = [];
const pendingEffects = [];
const effectCleanups = [];
const windowHandlers = {};
const revokedBlobUrls = [];
const focusEvents = [];
const previousFocus = { focus() { focusEvents.push("restored"); } };
const focusableFirst = { focus() { documentStub.activeElement = focusableFirst; focusEvents.push("first"); } };
const focusableLast = { focus() { documentStub.activeElement = focusableLast; focusEvents.push("last"); } };
const dialogNode = {
  focus() { documentStub.activeElement = dialogNode; focusEvents.push("dialog"); },
  querySelectorAll() { return [focusableFirst, focusableLast]; },
};
const pluginScript = { src: "https://dashboard.test/hermes/dashboard-plugins/@work/video-agent/dashboard/dist/index.js" };
const documentStub = {
  activeElement: previousFocus,
  getElementById() { return dialogNode; },
  querySelector(selector) { return selector === 'script[data-hermes-plugin="video-agent"]' ? pluginScript : null; },
};

global.document = documentStub;
global.URL.createObjectURL = (blob) => {
  assert.ok(blob instanceof Blob);
  return "blob:loaded";
};
global.URL.revokeObjectURL = (url) => { revokedBlobUrls.push(url); };

const React = {
  createElement(type, props, ...children) {
    return { type, props: props || {}, children };
  },
};

const hooks = {
  useState(initialValue) {
    const index = hookIndex++;
    if (!(index in stateValues)) stateValues[index] = initialValue;
    return [stateValues[index], (value) => {
      stateValues[index] = typeof value === "function" ? value(stateValues[index]) : value;
    }];
  },
  useEffect(callback) {
    pendingEffects.push(callback);
  },
  useCallback(callback) {
    return callback;
  },
  useRef(initialValue) {
    const index = hookIndex++;
    if (!(index in stateValues) || !stateValues[index] || typeof stateValues[index] !== "object" || !("current" in stateValues[index])) {
      stateValues[index] = { current: initialValue };
    }
    return stateValues[index];
  },
};

const windowStub = {
  __HERMES_AUTH_REQUIRED__: false,
  __HERMES_SESSION_TOKEN__: "test-session-token",
  __HERMES_BASE_PATH__: "/hermes",
  isSecureContext: true,
  location: { origin: "https://dashboard.test", href: "https://dashboard.test/" },
  navigator: { serviceWorker: {} },
  __HERMES_PLUGIN_SDK__: {
    React,
    hooks,
    fetchJSON: async (url, init) => {
      apiCalls.push({ url, init });
      if (init && init.method === "POST" && url.endsWith("/references")) {
        const payload = JSON.parse(init.body);
        return {
          reference: {
            id: "reference-1",
            platform: payload.url.includes("tiktok.com") ? "TikTok" : "YouTube",
            url: payload.url,
            note: payload.note,
            addedAt: "2026-01-01T12:00:00+00:00",
            observationChecklist,
          },
        };
      }
      if (init && init.method === "POST" && url.endsWith("/content/records%2Fwaiting-vod.mp4/items")) {
        const payload = JSON.parse(init.body);
        const item = {
          id: "draft-1",
          sourceFilename: waitingVideo.name,
          ...payload,
          createdAt: "2026-01-01T12:00:00+00:00",
          creationContext: { references: [savedReference], observationChecklist },
        };
        storedDrafts = [...storedDrafts, item];
        return { item };
      }
      if (init && init.method === "POST" && url.endsWith("/content/records%2Fwaiting-vod.mp4/moments")) {
        const payload = JSON.parse(init.body);
        const moment = {
          id: `moment-${storedMoments.length + 1}`,
          sourceFilename: waitingVideo.name,
          ...payload,
          createdAt: "2026-01-01T12:00:00+00:00",
        };
        storedMoments = [...storedMoments, moment];
        return { moment };
      }
      if (init && init.method === "POST" && url.endsWith("/references")) {
        const payload = JSON.parse(init.body);
        return { reference: { id: "reference-new", platform: "YouTube", ...payload, downloadStatus: "queued" } };
      }
      if (init && init.method === "POST" && url.endsWith("/references/reference-1/download")) {
        return { reference: { ...savedReference, downloadStatus: "queued" } };
      }
      if (init && init.method === "POST") {
        return {
          skill: { name: "clip-review", description: "Review finished clips." },
          requiresGatewayReload: true,
        };
      }
      if (init && init.method === "DELETE") return { removed: true };
      if (url.endsWith("/content/records%2Fwaiting-vod.mp4")) {
        return {
          sourceFilename: waitingVideo.name,
          items: storedDrafts,
          moments: storedMoments,
          clips: workspaceClips,
          clipsTruncated: false,
          references: [savedReference],
          observationChecklist,
        };
      }
      if (url.endsWith("/videos")) return snapshot;
      if (url.endsWith("/references")) return { references: [savedReference] };
      return { skills: [] };
    },
    authedFetch: async (url, init) => {
      mediaCalls.push({ url, init });
      return { ok: true, status: 200, blob: async () => new Blob(["video"], { type: "video/mp4" }) };
    },
  },
  __HERMES_PLUGINS__: {
    register(name, component) {
      assert.equal(name, "video-agent");
      registeredComponent = component;
    },
  },
  addEventListener(eventName, handler) { windowHandlers[eventName] = handler; },
  removeEventListener(eventName, handler) {
    if (windowHandlers[eventName] === handler) delete windowHandlers[eventName];
  },
  setInterval() { return 1; },
  clearInterval() {},
};

global.window = windowStub;
vm.runInThisContext(fs.readFileSync(bundlePath, "utf8"), { filename: bundlePath });
assert.equal(typeof registeredComponent, "function", "dashboard component should register");

function render(node) {
  if (Array.isArray(node)) return node.map(render);
  if (!node || typeof node !== "object") return node;
  if (typeof node.type === "function") return render(node.type(node.props));
  return { ...node, children: render(node.children) };
}

function walk(node, visit) {
  if (Array.isArray(node)) {
    for (const child of node) walk(child, visit);
    return;
  }
  if (!node || typeof node !== "object") return;
  visit(node);
  walk(node.children, visit);
}

function findAll(tree, predicate) {
  const matches = [];
  walk(tree, (node) => {
    if (predicate(node)) matches.push(node);
  });
  return matches;
}

function resetHooks(seed) {
  stateValues.length = 0;
  for (let index = 0; index < seed.length; index += 1) stateValues[index] = seed[index];
  hookIndex = 0;
  pendingEffects.length = 0;
}

async function flushEffects() {
  const effects = pendingEffects.splice(0);
  for (const effect of effects) {
    const cleanup = effect();
    if (typeof cleanup === "function") effectCleanups.push(cleanup);
  }
  await new Promise((resolve) => setTimeout(resolve, 0));
}

const finalVideo = { name: "final clip.mp4", sizeBytes: 20 };
const finishedClip = { name: "waiting-vod_scared_reaction_15s_TEST.mp4", sizeBytes: 24 };
const referenceVideo = { name: "vertical/style reference.mp4", sizeBytes: 18 };
let workspaceClips = [finishedClip];
const waitingVideo = { name: "records/waiting-vod.mp4", sizeBytes: 30 };
const observationChecklist = [
  { id: "hook", label: "Accroche et moment fort dans les premières secondes" },
  { id: "captions", label: "Texte à l’écran; sous-titres seulement si les paroles sont confirmées" },
];
const savedReference = {
  id: "reference-1",
  platform: "YouTube",
  url: "https://youtu.be/example123",
  note: "Observer l’accroche.",
  observationChecklist,
  downloadStatus: "not_requested",
};
let storedDrafts = [];
let storedMoments = [];
const snapshot = {
  readOnly: true,
  folders: [
    { id: "waiting", label: "Waiting", available: true, count: 1, videos: [waitingVideo] },
    { id: "references", label: "References", available: true, count: 1, videos: [referenceVideo] },
    { id: "finish", label: "Finish", available: true, count: 1, videos: [finalVideo] },
  ],
};

function baseState(selectedVideo = null, source = "") {
  return [snapshot, false, "", new Date("2026-01-01T12:00:00Z"), [], false, false, selectedVideo,
    null, "", "", "", false, null, source, false, ""];
}

async function main() {
  const dashboardManifest = JSON.parse(fs.readFileSync(path.join(__dirname, "../dashboard/manifest.json"), "utf8"));
  assert.equal(dashboardManifest.name, "video-agent", "dashboard plugin id should match the renamed package");
  assert.equal(dashboardManifest.label, "Agent Editor", "the dashboard tab should use the requested UI name");

  resetHooks(baseState());
  let tree = render(React.createElement(registeredComponent, {}));
  assert.equal(findAll(tree, (node) => node.type === "button" && node.props.className === "wvs__play").length, 2,
    "Finish and local References should expose play buttons in their collapsed libraries");
  const primaryLibrary = findAll(tree, (node) => node.type === "div" && node.props.className === "wvs__grid wvs__grid--primary")[0];
  assert.ok(primaryLibrary, "the Waiting recording list should be the primary home view");
  assert.equal(findAll(primaryLibrary, (node) => node.type === "section" && node.props.className === "wvs__panel").length, 1,
    "the home view should focus on Waiting rather than mixing source VODs and outputs");
  assert.equal(findAll(primaryLibrary, (node) => node.type === "div" && node.props.className === "wvs__thumbnail").length, 1,
    "each Waiting recording should have a thumbnail slot in the list");
  assert.ok(findAll(tree, (node) => node.type === "details" && node.props.className === "wvs__finish-library").length,
    "the complete Finish inventory should remain available in a secondary disclosure");
  assert.ok(findAll(tree, (node) => node.type === "details" && String(node.props.className).includes("wvs__reference-library")).length,
    "local reference videos should be available in their own secondary disclosure");
  assert.equal(findAll(tree, (node) => node.type === "h2" && node.props.id === "video-agent-skills-title").length, 1,
    "custom skills section should render");
  assert.equal(findAll(tree, (node) => node.type === "h2" && node.props.id === "video-agent-references-title").length, 1,
    "short-form reference section should render");
  assert.equal(findAll(tree, (node) => node.type === "form" && node.props.className === "wvs__reference-form").length, 1,
    "reference section should expose a link form");
  await flushEffects();
  hookIndex = 0;
  pendingEffects.length = 0;
  tree = render(React.createElement(registeredComponent, {}));
  const legacyDownloadButton = findAll(tree, (node) => node.type === "button" && node.props.className === "wvs__remove" && node.children?.includes("Télécharger"))[0];
  assert.ok(legacyDownloadButton, "an older saved reference with no local copy should expose a download button");
  await legacyDownloadButton.props.onClick();
  assert.ok(apiCalls.some((call) => call.url.endsWith("/references/reference-1/download") && call.init.method === "POST"),
    "downloading a saved reference should queue it through its API endpoint");
  hookIndex = 0;
  pendingEffects.length = 0;
  tree = render(React.createElement(registeredComponent, {}));
  assert.ok(findAll(tree, (node) => node.props && node.props.role === "status" && (node.children || []).includes("Téléchargement en attente…")).length > 0,
    "the UI should show queued status after requesting a local copy");

  const filenameButton = findAll(tree, (node) => node.type === "button" && String(node.props.className).includes("wvs__filename-button") && node.props.title === finalVideo.name)[0];
  assert.ok(filenameButton, "Finish filename should itself be clickable");
  filenameButton.props.onClick();
  assert.equal(stateValues[7], finalVideo, "clicking the Finish filename should select the video");

  const playButton = findAll(tree, (node) => node.type === "button" && node.props.className === "wvs__play")[0];
  playButton.props.onClick();
  assert.equal(stateValues[7], finalVideo, "clicking Lire should select the Finish video");

  resetHooks(baseState(finalVideo));
  tree = render(React.createElement(registeredComponent, {}));
  await flushEffects();
  assert.equal(mediaCalls.length, 1, "opening the player should fetch the video from the media API");
  assert.ok(mediaCalls[0].url.endsWith("/finish/final%20clip.mp4"), "video filename should be URL-encoded");
  assert.ok(mediaCalls[0].init.signal instanceof AbortSignal, "media request should be abortable");
  assert.ok(apiCalls.some((call) => call.url.endsWith("/videos")), "inventory should load through the JSON API");
  assert.ok(apiCalls.some((call) => call.url.endsWith("/skills")), "custom skills should load through the JSON API");
  assert.ok(apiCalls.some((call) => call.url.endsWith("/references")), "references should load from the local API");

  hookIndex = 0;
  pendingEffects.length = 0;
  tree = render(React.createElement(registeredComponent, {}));
  const player = findAll(tree, (node) => node.type === "video")[0];
  assert.ok(player, "selected Finish video should render a player");
  assert.equal(player.props.src, "blob:loaded");
  assert.equal(player.props.controls, true);
  assert.ok(findAll(tree, (node) => node.props && node.props.role === "dialog").length > 0,
    "player should be presented as a dialog");

  assert.equal(documentStub.activeElement, dialogNode, "opening the player should focus its dialog");
  let tabPrevented = false;
  documentStub.activeElement = focusableFirst;
  windowHandlers.keydown({ key: "Tab", shiftKey: true, preventDefault() { tabPrevented = true; } });
  assert.equal(tabPrevented, true, "the dialog should trap reverse tab navigation");
  assert.equal(documentStub.activeElement, focusableLast);
  documentStub.activeElement = focusableLast;
  windowHandlers.keydown({ key: "Tab", shiftKey: false, preventDefault() { tabPrevented = true; } });
  assert.equal(documentStub.activeElement, focusableFirst, "forward tab should wrap inside the dialog");
  windowHandlers.keydown({ key: "Escape" });
  assert.equal(stateValues[7], null, "Escape should close the player");

  for (const cleanup of effectCleanups.splice(0)) cleanup();
  assert.deepEqual(revokedBlobUrls, ["blob:loaded"], "closing should release the temporary video URL");
  assert.ok(focusEvents.includes("restored"), "closing should return focus to the previous element");

  resetHooks(baseState());
  tree = render(React.createElement(registeredComponent, {}));
  const referenceFilenameButton = findAll(tree, (node) => node.type === "button" && String(node.props.className).includes("wvs__filename-button") && node.props.title === referenceVideo.name)[0];
  assert.ok(referenceFilenameButton, "local reference video filename should be clickable");
  referenceFilenameButton.props.onClick();
  const selectedReferenceVideo = stateValues[7];
  assert.equal(selectedReferenceVideo.folderId, "references");

  resetHooks(baseState(selectedReferenceVideo));
  tree = render(React.createElement(registeredComponent, {}));
  await flushEffects();
  assert.equal(mediaCalls.length, 2, "opening a local reference should fetch it from the media API");
  assert.ok(mediaCalls[1].url.endsWith("/references/vertical%2Fstyle%20reference.mp4"),
    "local reference path segments should be encoded and use the References endpoint");
  for (const cleanup of effectCleanups.splice(0)) cleanup();
  assert.deepEqual(revokedBlobUrls, ["blob:loaded", "blob:loaded"], "closing should release both temporary video URLs");

  resetHooks(baseState());
  tree = render(React.createElement(registeredComponent, {}));
  await flushEffects();
  stateValues[13] = "clip-review";
  stateValues[14] = "Review finished clips.";
  stateValues[15] = "## Checklist\n- Keep the source intact.";
  hookIndex = 0;
  tree = render(React.createElement(registeredComponent, {}));
  const form = findAll(tree, (node) => node.type === "form" && node.props.className === "wvs__skill-form")[0];
  await form.props.onSubmit({ preventDefault() {} });
  const post = apiCalls.find((call) => call.url.endsWith("/skills") && call.init && call.init.method === "POST");
  assert.ok(post, "adding a skill should use the authenticated JSON API");
  assert.equal(post.url, "/api/plugins/video-agent/skills");
  assert.equal(JSON.parse(post.init.body).name, "clip-review");

  const referenceUrlInput = findAll(tree, (node) => node.type === "input" && node.props.id === "wvs-reference-url")[0];
  const referenceNoteInput = findAll(tree, (node) => node.type === "textarea" && node.props.id === "wvs-reference-note")[0];
  referenceUrlInput.props.onChange({ target: { value: "https://youtu.be/example123" } });
  referenceNoteInput.props.onChange({ target: { value: "Accroche et rythme." } });
  hookIndex = 0;
  pendingEffects.length = 0;
  tree = render(React.createElement(registeredComponent, {}));
  const referenceForm = findAll(tree, (node) => node.type === "form" && node.props.className === "wvs__reference-form")[0];
  await referenceForm.props.onSubmit({ preventDefault() {} });
  const referencePost = apiCalls.find((call) => call.url.endsWith("/references") && call.init && call.init.method === "POST");
  assert.ok(referencePost, "adding a video reference should use the authenticated JSON API");
  assert.deepEqual(JSON.parse(referencePost.init.body), {
    url: "https://youtu.be/example123",
    note: "Accroche et rythme.",
  });

  hookIndex = 0;
  pendingEffects.length = 0;
  tree = render(React.createElement(registeredComponent, {}));
  const referenceLink = findAll(tree, (node) => node.type === "a" && node.props.className === "wvs__reference-link")[0];
  assert.equal(referenceLink.props.target, "_blank");
  assert.equal(referenceLink.props.rel, "noopener noreferrer");
  const removeReferenceButton = findAll(tree, (node) => node.type === "button" && node.props.className === "wvs__remove" && node.children?.includes("Retirer"))[0];
  await removeReferenceButton.props.onClick();
  assert.ok(apiCalls.some((call) => call.url.endsWith("/references/reference-1") && call.init.method === "DELETE"),
    "reference removal should use the authenticated JSON API");

  storedDrafts = [];
  resetHooks(baseState());
  tree = render(React.createElement(registeredComponent, {}));
  const waitingButton = findAll(tree, (node) => node.type === "button" && String(node.props.className).includes("wvs__waiting-button"))[0];
  assert.ok(waitingButton, "Waiting recordings should open their own content workspace");
  waitingButton.props.onClick();
  assert.equal(stateValues[8], waitingVideo, "clicking a Waiting recording should select its content workspace");
  stateValues[9] = { loading: true, error: "", items: [], references: [], observationChecklist };
  stateValues[10] = { format: "short", title: "", concept: "", hook: "", script: "", notes: "" };
  stateValues[11] = false;
  stateValues[12] = null;

  hookIndex = 0;
  pendingEffects.length = 0;
  tree = render(React.createElement(registeredComponent, {}));
  await flushEffects();
  hookIndex = 0;
  pendingEffects.length = 0;
  tree = render(React.createElement(registeredComponent, {}));
  assert.equal(findAll(tree, (node) => node.type === "h1" && node.props.id === "wvs-content-title").length, 1,
    "the selected Waiting recording should show a dedicated content page");
  stateValues[9] = { ...stateValues[9], clips: [] };
  hookIndex = 0;
  pendingEffects.length = 0;
  tree = render(React.createElement(registeredComponent, {}));
  assert.ok(findAll(tree, (node) => node.type === "p" && (node.children || []).includes("Aucun clip rendu associé à cette rediffusion. Les repères enregistrés ne sont pas des fichiers vidéo.")).length,
    "the selected recording should explain when there is no rendered clip");
  stateValues[9] = { ...stateValues[9], clips: [finishedClip] };
  hookIndex = 0;
  pendingEffects.length = 0;
  tree = render(React.createElement(registeredComponent, {}));
  assert.ok(findAll(tree, (node) => node.type === "h2" && node.props.id === "wvs-finished-clips-title").length,
    "the selected recording should show its generated clips separately from markers");
  const clipPlayButton = findAll(tree, (node) => node.type === "button" && node.props.className === "wvs__clip-play")[0];
  assert.ok(clipPlayButton, "each generated clip should provide a preview action");
  assert.ok(findAll(tree, (node) => node.type === "a" && node.props.href === savedReference.url).length > 0,
    "the content page should show the saved reference");
  assert.ok(findAll(tree, (node) => node.type === "li" && (node.children || []).some((child) => typeof child === "string" && child.toLowerCase().includes("accroche"))).length > 0,
    "the content page should show observation points");
  const waitingFrame = findAll(tree, (node) => node.type === "iframe" && node.props.className === "wvs__waiting-video")[0];
  assert.ok(waitingFrame, "loopback token mode should use a scoped player bridge instead of downloading the complete VOD");
  assert.ok(waitingFrame.props.src.endsWith("/hermes/dashboard-plugins/@work/video-agent/media-player.html"),
    "the player bridge should come from the active profile's plugin assets");
  assert.equal(waitingFrame.props.src.includes("test-session-token"), false, "the session token must never appear in the player URL");
  const frameMessages = [];
  const frameWindow = { postMessage(data, origin) { frameMessages.push({ data, origin }); } };
  waitingFrame.props.ref.current = { contentWindow: frameWindow };
  waitingFrame.props.onLoad();
  assert.equal(frameMessages.length, 1, "the player frame should receive its authenticated stream configuration");
  assert.equal(frameMessages[0].origin, windowStub.location.origin, "the token must be sent only to the same origin");
  assert.equal(frameMessages[0].data.token, "test-session-token");
  assert.equal(frameMessages[0].data.filename, waitingVideo.name);
  assert.equal(frameMessages[0].data.apiUrl,
    "https://dashboard.test/hermes/api/plugins/video-agent/videos/waiting/records%2Fwaiting-vod.mp4?profile=work&codec=auto");
  assert.ok(frameMessages[0].data.streamUrl.endsWith("/hermes/dashboard-plugins/@work/video-agent/stream/records%2Fwaiting-vod.mp4"));
  assert.equal(typeof windowHandlers.message, "function", "the content workspace should listen for same-origin player messages");
  windowHandlers.message({
    origin: windowStub.location.origin,
    source: frameWindow,
    data: { type: "video-agent:player-ready", duration: 7200 },
  });
  windowHandlers.message({
    origin: windowStub.location.origin,
    source: frameWindow,
    data: { type: "video-agent:time-update", currentTime: 123.456, duration: 7200 },
  });
  hookIndex = 0;
  pendingEffects.length = 0;
  tree = render(React.createElement(registeredComponent, {}));
  assert.ok(findAll(tree, (node) => node.type === "time" && (node.children || []).includes("00:02:03")).length > 0,
    "the iframe player should update the moment-selection timecode in the parent workspace");

  windowStub.__HERMES_AUTH_REQUIRED__ = true;
  hookIndex = 0;
  pendingEffects.length = 0;
  tree = render(React.createElement(registeredComponent, {}));
  const waitingPlayer = findAll(tree, (node) => node.type === "video" && node.props.className === "wvs__waiting-video")[0];
  assert.ok(waitingPlayer, "the VOD should have a native player in protected-session mode");
  assert.equal(waitingPlayer.props.src, "/hermes/api/plugins/video-agent/videos/waiting/records%2Fwaiting-vod.mp4?profile=work&codec=auto");
  assert.equal(waitingPlayer.props.preload, "metadata", "the player should not preload the entire VOD");
  assert.equal(waitingPlayer.props.controls, true, "the player should expose seek controls");
  waitingPlayer.props.ref.current = {
    currentTime: 123.456,
    duration: 7200,
    play() { return Promise.resolve(); },
  };
  waitingPlayer.props.onLoadedMetadata({ currentTarget: waitingPlayer.props.ref.current });

  const momentNoteInput = findAll(tree, (node) => node.type === "textarea" && node.props.id === "wvs-moment-note")[0];
  const momentTargetInput = findAll(tree, (node) => node.type === "select" && node.props.id === "wvs-moment-target")[0];
  momentNoteInput.props.onChange({ target: { value: "Le retournement du duel." } });
  momentTargetInput.props.onChange({ target: { value: "short" } });
  hookIndex = 0;
  pendingEffects.length = 0;
  tree = render(React.createElement(registeredComponent, {}));
  const momentForm = findAll(tree, (node) => node.type === "form" && node.props.className === "wvs__moment-form")[0];
  await momentForm.props.onSubmit({ preventDefault() {} });
  const momentPost = apiCalls.find((call) => call.url.endsWith("/content/records%2Fwaiting-vod.mp4/moments") && call.init && call.init.method === "POST");
  assert.ok(momentPost, "marking a moment should persist it through the VOD-specific API");
  assert.deepEqual(JSON.parse(momentPost.init.body), {
    timeSeconds: 123.456,
    note: "Le retournement du duel.",
    targetFormat: "short",
  });
  assert.equal(storedMoments.length, 1, "the marker should be stored against the selected VOD");

  hookIndex = 0;
  pendingEffects.length = 0;
  tree = render(React.createElement(registeredComponent, {}));
  const seekButton = findAll(tree, (node) => node.type === "button" && node.props.className === "wvs__moment-seek")[0];
  assert.equal(seekButton.children[0], "00:02:03", "saved markers should show a readable timecode");
  const seekMessages = [];
  waitingPlayer.props.ref.current = { contentWindow: { postMessage(data, origin) { seekMessages.push({ data, origin }); } } };
  seekButton.props.onClick();
  assert.equal(seekMessages[0].data.type, "video-agent:seek", "clicking a saved marker should seek inside the authenticated iframe");
  assert.equal(seekMessages[0].data.timeSeconds, 123.456);
  assert.equal(seekMessages[0].origin, windowStub.location.origin);
  const addMomentButton = findAll(tree, (node) => node.type === "button" && node.props.className === "wvs__moment-add")[0];
  addMomentButton.props.onClick();
  assert.ok(stateValues[10].notes.includes("00:02:03 · Short — Le retournement du duel."),
    "a selected marker should be addable to the draft notes");

  hookIndex = 0;
  pendingEffects.length = 0;
  tree = render(React.createElement(registeredComponent, {}));
  const contentTitle = findAll(tree, (node) => node.type === "input" && node.props.id === "wvs-content-draft-title")[0];
  const contentConcept = findAll(tree, (node) => node.type === "textarea" && node.props.id === "wvs-content-draft-concept")[0];
  const contentFormat = findAll(tree, (node) => node.type === "select" && node.props.id === "wvs-content-draft-format")[0];
  contentTitle.props.onChange({ target: { value: "Retour dans la partie" } });
  contentConcept.props.onChange({ target: { value: "Montrer le retournement du duel." } });
  contentFormat.props.onChange({ target: { value: "short" } });
  hookIndex = 0;
  pendingEffects.length = 0;
  tree = render(React.createElement(registeredComponent, {}));
  const contentForm = findAll(tree, (node) => node.type === "form" && node.props.className === "wvs__content-form")[0];
  await contentForm.props.onSubmit({ preventDefault() {} });
  const contentPost = apiCalls.find((call) => call.url.endsWith("/content/records%2Fwaiting-vod.mp4/items") && call.init && call.init.method === "POST");
  assert.ok(contentPost, "creating a content draft should post to the VOD-specific API");
  assert.deepEqual(JSON.parse(contentPost.init.body), {
    format: "short",
    title: "Retour dans la partie",
    concept: "Montrer le retournement du duel.",
    hook: "",
    script: "",
    notes: "00:02:03 · Short — Le retournement du duel.",
  });
  assert.ok(storedDrafts[0].creationContext.references.some((reference) => reference.id === savedReference.id),
    "new content drafts should carry a snapshot of the saved references");
  assert.deepEqual(storedDrafts[0].creationContext.references[0].observationChecklist, observationChecklist,
    "the reference observation checklist should be frozen into the content draft");
  assert.deepEqual(storedDrafts[0].creationContext.observationChecklist, observationChecklist,
    "standard observation points should be frozen into the content draft");
  hookIndex = 0;
  pendingEffects.length = 0;
  tree = render(React.createElement(registeredComponent, {}));
  assert.equal(findAll(tree, (node) => node.type === "h3" && (node.children || []).includes("Retour dans la partie")).length, 1,
    "the saved content draft should appear in the selected VOD workspace");
  const previewClipButton = findAll(tree, (node) => node.type === "button" && node.props.className === "wvs__clip-play")[0];
  previewClipButton.props.onClick();
  assert.equal(stateValues[7], finishedClip, "previewing a generated clip should open that Finish video");
  hookIndex = 0;
  pendingEffects.length = 0;
  tree = render(React.createElement(registeredComponent, {}));
  await flushEffects();
  assert.ok(mediaCalls.some((call) => call.url.endsWith(`/finish/${encodeURIComponent(finishedClip.name)}`)),
    "a generated clip preview should use the authenticated Finish video endpoint");
  assert.ok(findAll(tree, (node) => node.props && node.props.role === "dialog").length > 0,
    "the generated clip should open in the accessible video player dialog");
}

main().then(() => {
  console.log("Dashboard bundle UI smoke tests passed.");
}).catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
