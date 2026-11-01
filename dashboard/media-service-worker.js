"use strict";

const mediaAuthorizationByClient = new Map();
const sessionHeader = "X-Hermes-Session-Token";
const maxTokenLength = 4096;

function reply(status, message) {
  return new Response(message, {
    status,
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
  });
}

function validFilename(filename) {
  if (typeof filename !== "string" || filename.length === 0 || filename.length > 1024
      || filename.includes("\\") || /[\u0000-\u001f\u007f]/.test(filename)
      || !filename.toLowerCase().endsWith(".mp4")) return false;
  const parts = filename.split("/");
  return parts.length <= 16 && parts.every((part) => part && part !== "." && part !== ".." && part.length <= 255);
}

function validateApiUrl(rawUrl, filename) {
  let url;
  try {
    url = new URL(rawUrl, self.location.origin);
  } catch (_error) {
    return null;
  }
  if (url.origin !== self.location.origin || url.username || url.password || url.hash || !validFilename(filename)) return null;
  const routeSuffix = `/api/plugins/waiting-studio/videos/waiting/${encodeURIComponent(filename)}`;
  const routeIndex = url.pathname.lastIndexOf("/api/plugins/waiting-studio/videos/waiting/");
  if (routeIndex < 0 || url.pathname !== `${url.pathname.slice(0, routeIndex)}${routeSuffix}`) return null;
  if ([...url.searchParams.keys()].some((key) => !["profile", "codec"].includes(key))
      || url.searchParams.getAll("profile").length > 1
      || url.searchParams.getAll("codec").length > 1
      || (url.searchParams.has("codec") && !["auto", "source"].includes(url.searchParams.get("codec")))) return null;
  return url.href;
}

function filenameFromStreamUrl(url) {
  const scopePath = new URL(self.registration.scope).pathname;
  const prefix = `${scopePath}stream/`;
  if (url.origin !== self.location.origin || url.search || url.hash || !url.pathname.startsWith(prefix)) return null;
  let filename;
  try {
    filename = decodeURIComponent(url.pathname.slice(prefix.length));
  } catch (_error) {
    return null;
  }
  return validFilename(filename) ? filename : null;
}

async function requestClientAuthorization(client, filename) {
  if (!client || typeof client.postMessage !== "function" || typeof MessageChannel !== "function") return null;
  return new Promise((resolve) => {
    const channel = new MessageChannel();
    let completed = false;
    const finish = (authorization) => {
      if (completed) return;
      completed = true;
      clearTimeout(timeout);
      channel.port1.close();
      resolve(authorization);
    };
    const timeout = setTimeout(() => finish(null), 5000);
    channel.port1.onmessage = (event) => {
      const data = event.data;
      if (!data || data.type !== "waiting-studio:media-auth-response" || data.filename !== filename) {
        finish(null);
        return;
      }
      const apiUrl = validateApiUrl(data.apiUrl, filename);
      if (typeof data.token !== "string" || data.token.length === 0 || data.token.length > maxTokenLength || !apiUrl) {
        finish(null);
        return;
      }
      finish({ token: data.token, filename, apiUrl });
    };
    try {
      client.postMessage({ type: "waiting-studio:request-media-auth", filename }, [channel.port2]);
    } catch (_error) {
      finish(null);
    }
  });
}

async function proxyMedia(request, clientId) {
  if (request.method !== "GET" && request.method !== "HEAD") return reply(405, "Method not allowed");
  const filename = filenameFromStreamUrl(new URL(request.url));
  if (!filename) return reply(404, "Media not found");
  const client = clientId ? await self.clients.get(clientId) : null;
  if (!client) {
    mediaAuthorizationByClient.delete(clientId);
    return reply(401, "Unauthorized");
  }
  let authorization = mediaAuthorizationByClient.get(clientId);
  if (!authorization || authorization.filename !== filename) {
    authorization = await requestClientAuthorization(client, filename);
    if (!authorization) return reply(401, "Unauthorized");
    mediaAuthorizationByClient.set(clientId, authorization);
  }

  const headers = new Headers();
  for (const name of ["range", "if-range", "accept"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  headers.set(sessionHeader, authorization.token);
  return fetch(new Request(authorization.apiUrl, {
    method: request.method,
    headers,
    mode: "same-origin",
    credentials: "same-origin",
    cache: "no-store",
    redirect: "error",
  }));
}

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("message", (event) => {
  const clientId = event.source && event.source.id;
  if (!clientId || !event.data || typeof event.data !== "object") return;
  if (event.data.type === "waiting-studio:clear-media-auth") {
    mediaAuthorizationByClient.delete(clientId);
    return;
  }
  if (event.data.type !== "waiting-studio:set-media-auth") return;

  const { token, filename } = event.data;
  const apiUrl = validateApiUrl(event.data.apiUrl, filename);
  if (typeof token !== "string" || token.length === 0 || token.length > maxTokenLength || !apiUrl) {
    mediaAuthorizationByClient.delete(clientId);
    return;
  }
  mediaAuthorizationByClient.set(clientId, { token, filename, apiUrl });
  if (event.ports && event.ports[0]) {
    event.ports[0].postMessage({ type: "waiting-studio:media-auth-ready" });
  }
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const scopePath = new URL(self.registration.scope).pathname;
  if (url.origin !== self.location.origin || !url.pathname.startsWith(`${scopePath}stream/`)) return;
  event.respondWith(proxyMedia(event.request, event.clientId));
});
