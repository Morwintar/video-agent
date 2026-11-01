const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const pluginPath = path.join(__dirname, "../desktop/plugin.js");
assert.ok(fs.existsSync(pluginPath), "Desktop plugin entry should exist");
const source = fs.readFileSync(pluginPath, "utf8");
const executable = source
  .replace(/^import .*;?\s*$/gm, "")
  .replace(/export function (parseTimecode|formatTimecode)/g, "function $1")
  .replace(/export default\s*\{/g, "module.exports.default = {")
  + "\nmodule.exports.parseTimecode = parseTimecode;\nmodule.exports.formatTimecode = formatTimecode;\n";
const moduleStub = { exports: {} };
const sandbox = {
  module: moduleStub,
  ROUTES_AREA: "routes",
  SIDEBAR_NAV_AREA: "sidebar.nav",
  host: {},
  React: {},
  jsx: () => null,
  jsxs: () => null,
};
vm.runInNewContext(executable, sandbox, { filename: pluginPath });

const plugin = moduleStub.exports.default;
assert.equal(plugin.id, "video-agent");
assert.equal(plugin.name, "Agent Editor");
const registrations = [];
const apiCalls = [];
const hookStates = new Map();
const mutationCalls = [];
let currentComponent = null;
let hookIndex = 0;
const sampleVideo = { name: "records/waiting-vod.mp4", sizeBytes: 1024 };
const sampleFolders = [
  { id: "waiting", label: "Waiting", available: true, count: 1, videos: [sampleVideo] },
  { id: "references", label: "References", available: true, count: 0, videos: [] },
  { id: "finish", label: "Finish", available: true, count: 0, videos: [] },
];
sandbox.React = {
  useState(initialValue) {
    const componentStates = hookStates.get(currentComponent) || [];
    hookStates.set(currentComponent, componentStates);
    const index = hookIndex++;
    if (!(index in componentStates)) componentStates[index] = initialValue;
    return [componentStates[index], value => { componentStates[index] = value; }];
  },
  useEffect() {},
};
sandbox.jsx = (type, props, key) => ({ type, props: props || {}, key });
sandbox.jsxs = sandbox.jsx;
sandbox.useQuery = ({ queryKey }) => {
  if (queryKey[1] === "videos") return { data: { folders: sampleFolders }, isLoading: false, refetch() {} };
  if (queryKey[1] === "references") return { data: { references: [] }, isLoading: false, refetch() {} };
  return { data: { moments: [] }, isLoading: false, error: null, refetch() {} };
};
sandbox.useMutation = options => ({
  mutate(payload) {
    mutationCalls.push(payload);
    const result = options.mutationFn(payload);
    Promise.resolve(result).then(() => options.onSuccess?.());
    return result;
  },
  isPending: false,
  error: null,
  reset() {},
});
sandbox.useQueryClient = () => ({ invalidateQueries() {} });
plugin.register({
  register: (item) => registrations.push(item),
  rest: (url, options) => {
    apiCalls.push({ url, options });
    return Promise.resolve({ moment: {}, reference: {} });
  },
  os: { openExternal() {} },
});
assert.ok(registrations.some((item) => item.area === "routes" && item.data.path === "/agent-editor"));
assert.ok(registrations.some((item) => item.area === "sidebar.nav" && item.data.path === "/agent-editor" && item.data.label === "Agent Editor"));

function render(node) {
  if (Array.isArray(node)) return node.map(render);
  if (!node || typeof node !== "object") return node;
  if (typeof node.type === "function") {
    const previousComponent = currentComponent;
    const previousHookIndex = hookIndex;
    currentComponent = node.type;
    hookIndex = 0;
    const child = node.type(node.props);
    currentComponent = previousComponent;
    hookIndex = previousHookIndex;
    return render(child);
  }
  return { ...node, props: { ...node.props, children: render(node.props.children) } };
}
function collectText(node, output = []) {
  if (Array.isArray(node)) node.forEach(child => collectText(child, output));
  else if (node && typeof node === "object") collectText(node.props?.children, output);
  else if (typeof node === "string") output.push(node);
  return output.join(" ");
}
function findNode(node, predicate) {
  if (Array.isArray(node)) {
    for (const child of node) {
      const match = findNode(child, predicate);
      if (match) return match;
    }
  } else if (node && typeof node === "object") {
    if (predicate(node)) return node;
    return findNode(node.props?.children, predicate);
  }
  return null;
}
const pageRegistration = registrations.find(item => item.area === "routes");
const renderPage = () => render(pageRegistration.render());
let page = renderPage();
let pageText = collectText(page);
assert.match(pageText, /Agent Editor/);
assert.match(pageText, /À traiter/);
assert.match(pageText, /waiting-vod\.mp4/);
assert.match(pageText, /Repères/);

const timecodeInput = findNode(page, node => node.type === "input" && node.props.id === "agent-editor-timecode");
const momentNoteInput = findNode(page, node => node.type === "input" && node.props.id === "agent-editor-note");
assert.ok(timecodeInput && momentNoteInput, "Moment editor controls should render");
timecodeInput.props.onChange({ target: { value: "00:01:02.5" } });
momentNoteInput.props.onChange({ target: { value: "Bonne réaction" } });
page = renderPage();
const momentForm = findNode(page, node => node.type === "form" && typeof node.props.onSubmit === "function");
momentForm.props.onSubmit({ preventDefault() {} });
assert.equal(mutationCalls.length, 1);
assert.deepEqual(JSON.parse(JSON.stringify(apiCalls[0])), {
  url: "/content/records%2Fwaiting-vod.mp4/moments",
  options: { method: "POST", body: { timeSeconds: 62.5, note: "Bonne réaction", targetFormat: "both" } },
});

const referenceTab = findNode(page, node => node.type === "button" && collectText(node).includes("Références"));
assert.ok(referenceTab, "References tab should render");
referenceTab.props.onClick();
page = renderPage();
const referenceUrlInput = findNode(page, node => node.type === "input" && node.props.id === "agent-editor-reference-url");
const referenceNoteInput = findNode(page, node => node.type === "textarea" && node.props.id === "agent-editor-reference-note");
assert.ok(referenceUrlInput && referenceNoteInput, "Reference controls should render");
referenceUrlInput.props.onChange({ target: { value: "https://youtu.be/example" } });
referenceNoteInput.props.onChange({ target: { value: "Rythme et cadrage" } });
page = renderPage();
const referenceForm = findNode(page, node => node.type === "form" && typeof node.props.onSubmit === "function");
referenceForm.props.onSubmit({ preventDefault() {} });
assert.deepEqual(JSON.parse(JSON.stringify(apiCalls[1])), {
  url: "/references",
  options: { method: "POST", body: { url: "https://youtu.be/example", note: "Rythme et cadrage" } },
});
assert.equal(mutationCalls.length, 2);

assert.equal(moduleStub.exports.parseTimecode("01:02"), 62);
assert.equal(moduleStub.exports.parseTimecode("01:02:03.5"), 3723.5);
assert.equal(moduleStub.exports.parseTimecode("24:00:00"), 86400);
assert.equal(moduleStub.exports.parseTimecode("1:60"), null);
assert.equal(moduleStub.exports.parseTimecode("24:00:01"), null);
assert.equal(moduleStub.exports.parseTimecode("00:00:00.1234"), null);
assert.equal(moduleStub.exports.formatTimecode(3723.5), "01:02:03.500");

console.log("Desktop plugin contract and timecode tests passed");
