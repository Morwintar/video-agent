"use strict";

(() => {
  const video = document.getElementById("waiting-studio-video");
  const status = document.getElementById("status");
  const parentOrigin = window.location.origin;
  let initialized = false;
  let workerAuthorizationHandler = null;

  function showStatus(message) {
    status.textContent = message;
    status.hidden = false;
  }

  function report(type, fields) {
    window.parent.postMessage({ type, ...fields }, parentOrigin);
  }

  function validateConfig(raw) {
    if (!raw || typeof raw !== "object") throw new Error("Configuration de lecture manquante.");
    const filename = raw.filename;
    const token = raw.token;
    const filenameParts = typeof filename === "string" ? filename.split("/") : [];
    if (typeof filename !== "string" || filename.length > 1024 || filename.includes("\\")
        || filenameParts.length > 16 || filenameParts.some((part) => !part || part === "." || part === ".." || part.length > 255)
        || /[\u0000-\u001f\u007f]/.test(filename) || !filename.toLowerCase().endsWith(".mp4")) {
      throw new Error("Nom de rediffusion invalide.");
    }
    if (typeof token !== "string" || !token || token.length > 4096) throw new Error("Session Dashboard indisponible.");

    const apiUrl = new URL(raw.apiUrl, parentOrigin);
    const routeSuffix = `/api/plugins/waiting-studio/videos/waiting/${encodeURIComponent(filename)}`;
    const routeIndex = apiUrl.pathname.lastIndexOf("/api/plugins/waiting-studio/videos/waiting/");
    if (apiUrl.origin !== parentOrigin || apiUrl.username || apiUrl.password || apiUrl.hash || routeIndex < 0
        || apiUrl.pathname !== `${apiUrl.pathname.slice(0, routeIndex)}${routeSuffix}`) {
      throw new Error("Adresse de rediffusion invalide.");
    }
    if ([...apiUrl.searchParams.keys()].some((key) => !["profile", "codec"].includes(key))
        || apiUrl.searchParams.getAll("profile").length > 1
        || apiUrl.searchParams.getAll("codec").length > 1
        || (apiUrl.searchParams.has("codec") && !["auto", "source"].includes(apiUrl.searchParams.get("codec")))) {
      throw new Error("Contexte de profil invalide.");
    }

    const scopeUrl = new URL("./", window.location.href);
    const expectedStreamUrl = new URL(`stream/${encodeURIComponent(filename)}`, scopeUrl);
    const streamUrl = new URL(raw.streamUrl, parentOrigin);
    if (streamUrl.origin !== parentOrigin || streamUrl.href !== expectedStreamUrl.href) {
      throw new Error("Adresse de lecture invalide.");
    }
    return { apiUrl: apiUrl.href, filename, token, streamUrl: streamUrl.href, scopeUrl };
  }

  function waitForActiveWorker(registration, workerUrl) {
    const matchesWorker = (worker) => worker && new URL(worker.scriptURL).href === workerUrl.href;
    if (registration.active && registration.active.state === "activated") return Promise.resolve();
    return new Promise((resolve, reject) => {
      const candidate = registration.installing || registration.waiting || registration.active;
      if (!candidate) {
        reject(new Error("Le service worker du lecteur ne s’est pas activé."));
        return;
      }
      const timeout = window.setTimeout(() => reject(new Error("Délai dépassé pendant la préparation du lecteur.")), 10000);
      const check = () => {
        if (registration.active && registration.active.state === "activated" && matchesWorker(registration.active)) {
          window.clearTimeout(timeout);
          resolve();
        } else if (candidate.state === "redundant") {
          window.clearTimeout(timeout);
          reject(new Error("Le service worker du lecteur ne peut pas démarrer."));
        }
      };
      candidate.addEventListener("statechange", check);
      check();
    });
  }

  function waitForController(workerUrl) {
    return new Promise((resolve, reject) => {
      const isReady = () => {
        const controller = navigator.serviceWorker.controller;
        return controller && new URL(controller.scriptURL).href === workerUrl.href ? controller : null;
      };
      const current = isReady();
      if (current) {
        resolve(current);
        return;
      }
      const timeout = window.setTimeout(() => {
        navigator.serviceWorker.removeEventListener("controllerchange", check);
        reject(new Error("Le navigateur n’a pas confié le lecteur au service worker."));
      }, 10000);
      const check = () => {
        const controller = isReady();
        if (!controller) return;
        window.clearTimeout(timeout);
        navigator.serviceWorker.removeEventListener("controllerchange", check);
        resolve(controller);
      };
      navigator.serviceWorker.addEventListener("controllerchange", check);
      check();
    });
  }

  function authorizeController(controller, config) {
    return new Promise((resolve, reject) => {
      const channel = new MessageChannel();
      const timeout = window.setTimeout(() => {
        channel.port1.close();
        reject(new Error("Le lecteur n’a pas accepté l’autorisation de session."));
      }, 5000);
      channel.port1.onmessage = (event) => {
        window.clearTimeout(timeout);
        channel.port1.close();
        if (event.data && event.data.type === "waiting-studio:media-auth-ready") resolve();
        else reject(new Error("Le lecteur a refusé l’autorisation de session."));
      };
      controller.postMessage({
        type: "waiting-studio:set-media-auth",
        token: config.token,
        filename: config.filename,
        apiUrl: config.apiUrl,
      }, [channel.port2]);
    });
  }

  async function startPlayer(rawConfig) {
    if (initialized) return;
    initialized = true;
    showStatus("Préparation de la lecture progressive…");
    try {
      const config = validateConfig(rawConfig);
      if (!window.isSecureContext || !navigator.serviceWorker || typeof MessageChannel !== "function") {
        throw new Error("Le navigateur doit être ouvert sur localhost ou en HTTPS pour transmettre la session de façon sécuritaire.");
      }
      const workerUrl = new URL("media-service-worker.js", config.scopeUrl);
      const registration = await navigator.serviceWorker.register(workerUrl.href, {
        scope: config.scopeUrl.pathname,
        updateViaCache: "none",
      });
      await waitForActiveWorker(registration, workerUrl);
      const controller = await waitForController(workerUrl);
      if (workerAuthorizationHandler) navigator.serviceWorker.removeEventListener("message", workerAuthorizationHandler);
      workerAuthorizationHandler = (event) => {
        if (event.source !== navigator.serviceWorker.controller || !event.data
            || event.data.type !== "waiting-studio:request-media-auth"
            || event.data.filename !== config.filename || !event.ports || !event.ports[0]) return;
        event.ports[0].postMessage({
          type: "waiting-studio:media-auth-response",
          token: config.token,
          filename: config.filename,
          apiUrl: config.apiUrl,
        });
      };
      navigator.serviceWorker.addEventListener("message", workerAuthorizationHandler);
      await authorizeController(controller, config);

      video.addEventListener("loadedmetadata", () => {
        status.hidden = true;
        const duration = Number.isFinite(video.duration) ? video.duration : null;
        report("waiting-studio:player-ready", { duration });
        report("waiting-studio:time-update", { currentTime: video.currentTime, duration });
      });
      video.addEventListener("timeupdate", () => {
        report("waiting-studio:time-update", {
          currentTime: video.currentTime,
          duration: Number.isFinite(video.duration) ? video.duration : null,
        });
      });
      video.addEventListener("error", () => {
        showStatus("Impossible de lire la rediffusion. Vérifie la session du Dashboard et réessaie.");
        report("waiting-studio:player-error", {});
      });
      window.addEventListener("message", (event) => {
        if (event.origin !== parentOrigin || event.source !== window.parent || !event.data || typeof event.data !== "object") return;
        if (event.data.type !== "waiting-studio:seek" || !Number.isFinite(event.data.timeSeconds)) return;
        if (event.data.timeSeconds < 0 || (Number.isFinite(video.duration) && event.data.timeSeconds > video.duration)) return;
        video.currentTime = event.data.timeSeconds;
      });
      video.src = config.streamUrl;
      video.load();
      showStatus("Chargement des métadonnées de la rediffusion…");
    } catch (_error) {
      initialized = false;
      showStatus(_error && _error.message ? _error.message : "Impossible de préparer le lecteur.");
      report("waiting-studio:player-error", {});
    }
  }

  window.addEventListener("message", (event) => {
    if (event.origin !== parentOrigin || event.source !== window.parent || !event.data || event.data.type !== "waiting-studio:init-media-player") return;
    startPlayer(event.data);
  });

  window.addEventListener("pagehide", () => {
    if (workerAuthorizationHandler && navigator.serviceWorker) {
      navigator.serviceWorker.removeEventListener("message", workerAuthorizationHandler);
      workerAuthorizationHandler = null;
    }
    const controller = navigator.serviceWorker && navigator.serviceWorker.controller;
    if (controller) controller.postMessage({ type: "waiting-studio:clear-media-auth" });
  });
})();
