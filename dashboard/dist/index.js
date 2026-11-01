(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;

  const React = SDK.React;
  const h = React.createElement;
  const { useState, useEffect, useCallback, useRef } = SDK.hooks;
  const PLUGIN_SCRIPT = document.currentScript || document.querySelector('script[data-hermes-plugin="video-agent"]');
  const API_URL = "/api/plugins/video-agent/videos";
  const SKILLS_URL = "/api/plugins/video-agent/skills";
  const REFERENCES_URL = "/api/plugins/video-agent/references";
  const CONTENT_URL = "/api/plugins/video-agent/content";

  function formatBytes(bytes) {
    if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes < 0) return "";
    const units = ["o", "Kio", "Mio", "Gio", "Tio"];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit += 1;
    }
    const formatted = new Intl.NumberFormat("fr-CA", {
      maximumFractionDigits: unit === 0 ? 0 : 1,
    }).format(value);
    return `${formatted} ${units[unit]}`;
  }

  function pluralizeVideo(count) {
    return `${count} vidéo${count === 1 ? "" : "s"}`;
  }

  function formatTimecode(seconds) {
    if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "00:00:00";
    const totalSeconds = Math.floor(seconds);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const remainder = totalSeconds % 60;
    return [hours, minutes, remainder].map((part) => String(part).padStart(2, "0")).join(":");
  }

  function momentTargetLabel(targetFormat) {
    if (targetFormat === "short") return "Short";
    if (targetFormat === "video") return "Vidéo";
    return "Short + vidéo";
  }

  function waitingStudioAssetLocation() {
    const pluginScript = PLUGIN_SCRIPT;
    if (!pluginScript || !pluginScript.src) return null;
    let scriptUrl;
    try {
      scriptUrl = new URL(pluginScript.src, window.location.href);
    } catch (_error) {
      return null;
    }
    const marker = "/dashboard-plugins/";
    const markerIndex = scriptUrl.pathname.indexOf(marker);
    if (markerIndex < 0) return null;
    const assetPath = scriptUrl.pathname.slice(markerIndex + marker.length);
    const pluginMatch = /^(?:@([^/]+)\/)?video-agent\//.exec(assetPath);
    if (!pluginMatch) return null;
    const rootPath = scriptUrl.pathname.slice(0, markerIndex + marker.length + pluginMatch[0].length);
    let profile = "";
    if (pluginMatch[1]) {
      try {
        profile = decodeURIComponent(pluginMatch[1]);
      } catch (_error) {
        return null;
      }
    }
    return { assetRoot: new URL(rootPath, scriptUrl.origin).href, profile };
  }

  function waitingVideoApiUrl(filename, profile) {
    const rawBasePath = window.__HERMES_BASE_PATH__ || "";
    const basePath = rawBasePath ? "/" + rawBasePath.split("/").filter(Boolean).join("/") : "";
    const url = new URL(`${basePath}/api/plugins/video-agent/videos/waiting/${encodeURIComponent(filename)}`, window.location.origin);
    if (profile) url.searchParams.set("profile", profile);
    url.searchParams.set("codec", "auto");
    return `${url.pathname}${url.search}`;
  }

  function VideoThumbnail(props) {
    const [visible, setVisible] = useState(false);
    const [imageUrl, setImageUrl] = useState("");
    const [failed, setFailed] = useState(false);
    const thumbnailRef = useRef(null);

    useEffect(() => {
      const element = thumbnailRef.current;
      if (!element) return undefined;
      if (typeof window.IntersectionObserver !== "function") {
        setVisible(true);
        return undefined;
      }
      const observer = new window.IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      }, { rootMargin: "160px" });
      observer.observe(element);
      return () => observer.disconnect();
    }, []);

    useEffect(() => {
      if (!visible) return undefined;
      const controller = new AbortController();
      let objectUrl = "";
      async function loadThumbnail() {
        try {
          const url = `${API_URL}/waiting-thumbnails/${encodeURIComponent(props.video.name)}`;
          const response = await SDK.authedFetch(url, { signal: controller.signal });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const imageBlob = await response.blob();
          if (controller.signal.aborted) return;
          objectUrl = URL.createObjectURL(imageBlob);
          setImageUrl(objectUrl);
        } catch (_error) {
          if (!controller.signal.aborted) setFailed(true);
        }
      }
      loadThumbnail();
      return () => {
        controller.abort();
        if (objectUrl) URL.revokeObjectURL(objectUrl);
      };
    }, [visible, props.video.name]);

    return h("div", { className: "wvs__thumbnail", ref: thumbnailRef, "aria-hidden": "true" },
      imageUrl
        ? h("img", { src: imageUrl, alt: "", loading: "lazy" })
        : h("span", { className: "wvs__thumbnail-placeholder" }, failed ? "Aperçu indisponible" : "Aperçu vidéo"),
    );
  }

  function VideoSection(props) {
    const folder = props.folder;
    const videos = Array.isArray(folder.videos) ? folder.videos : [];
    const count = Number.isInteger(folder.count) ? folder.count : videos.length;
    const titleId = `video-agent-library-${folder.id}-title`;
    const isPlayable = folder.id === "finish" || folder.id === "references";

    let content;
    if (props.loading && !props.hasSnapshot) {
      content = h(
        "div",
        { className: "wvs__loading", role: "status", "aria-label": `Chargement de ${folder.label}` },
        h("span", { className: "wvs__skeleton" }),
        h("span", { className: "wvs__skeleton wvs__skeleton--short" }),
        h("span", { className: "wvs__skeleton" }),
      );
    } else if (props.error && !props.hasSnapshot) {
      content = h("p", { className: "wvs__empty", role: "status" }, "La liste n’a pas pu être chargée.");
    } else if (!folder.available) {
      content = h("p", { className: "wvs__empty", role: "status" }, "Ce dossier n’est pas accessible depuis le dashboard.");
    } else if (videos.length === 0) {
      content = h("p", { className: "wvs__empty", role: "status" }, "Aucun MP4 dans ce dossier.");
    } else {
      content = h(
        "ul",
        { className: "wvs__list", "aria-label": `Vidéos ${folder.label}` },
        videos.map((video) => {
          const playVideo = () => props.onPlay(
            folder.id === "references" ? { ...video, folderId: "references" } : video,
          );
          return h(
            "li",
            { className: `wvs__item${folder.id === "waiting" ? " wvs__item--waiting" : ""}`, key: `${folder.id}:${video.name}` },
            folder.id === "waiting" ? h(VideoThumbnail, { video, key: `thumbnail:${video.name}` }) : null,
            h("span", { className: "wvs__file-mark", "aria-hidden": "true" }, "MP4"),
            h(
              "span",
              { className: "wvs__file-details" },
              isPlayable
                ? h("button", {
                    className: "wvs__filename wvs__filename-button",
                    type: "button",
                    title: video.name,
                    onClick: playVideo,
                    "aria-label": `Lire ${video.name}`,
                  }, video.name)
                : folder.id === "waiting"
                  ? h("button", {
                      className: "wvs__filename wvs__filename-button wvs__waiting-button",
                      type: "button",
                      title: video.name,
                      onClick: () => props.onOpenContent(video),
                      "aria-label": `Ouvrir le contenu de la rediffusion ${video.name}`,
                    }, video.name)
                  : h("span", { className: "wvs__filename", title: video.name }, video.name),
              h("span", { className: "wvs__filesize" }, formatBytes(video.sizeBytes)),
            ),
            isPlayable
              ? h(
                  "button",
                  {
                    className: "wvs__play",
                    type: "button",
                    onClick: playVideo,
                    "aria-label": `Lire ${video.name}`,
                  },
                  "Lire",
                )
              : null,
          );
        }),
      );
    }

    const subtitle = folder.id === "finish"
      ? "MP4 · cliquez sur le nom ou sur Lire pour prévisualiser"
      : folder.id === "waiting"
        ? "Cliquez sur une rediffusion pour ouvrir son espace de contenu"
        : folder.id === "references"
          ? "Vidéos locales d’inspiration · lecture en prévisualisation"
          : "Fichiers MP4";

    return h(
      "section",
      { className: "wvs__panel", "aria-labelledby": titleId },
      h(
        "header",
        { className: "wvs__panel-header" },
        h(
          "div",
          null,
          h("h2", { className: "wvs__panel-title", id: titleId }, folder.label),
          h("p", { className: "wvs__panel-subtitle" }, subtitle),
        ),
        h("span", { className: "wvs__count" }, pluralizeVideo(count)),
      ),
      h("div", { className: "wvs__panel-content" }, content),
    );
  }

  function FinishedClipsSection(props) {
    const clips = Array.isArray(props.clips) ? props.clips : [];
    return h(
      "section",
      { className: "wvs__panel wvs__finished-clips", "aria-labelledby": "wvs-finished-clips-title" },
      h(
        "header",
        { className: "wvs__panel-header" },
        h("div", null,
          h("h2", { className: "wvs__panel-title", id: "wvs-finished-clips-title" }, "Clips créés"),
          h("p", { className: "wvs__panel-subtitle" }, "MP4 de Finish associés par le nom de cette rediffusion; les repères restent des notes, pas des vidéos."),
        ),
        h("span", { className: "wvs__count" }, `${clips.length} clip${clips.length === 1 ? "" : "s"}`),
      ),
      props.loading
        ? h("p", { className: "wvs__status", role: "status" }, "Recherche des clips créés pour cette rediffusion…")
        : props.error
          ? h("p", { className: "wvs__status", role: "status" }, "La liste des clips n’a pas pu être chargée; réessaie l’actualisation.")
          : clips.length
            ? h("ul", { className: "wvs__clip-list", "aria-label": "Clips créés dans Finish" }, clips.map((clip) =>
                h("li", { className: "wvs__clip-item", key: clip.name },
                  h("span", { className: "wvs__file-mark", "aria-hidden": "true" }, "MP4"),
                  h("span", { className: "wvs__file-details" },
                    h("span", { className: "wvs__filename", title: clip.name }, clip.name),
                    h("span", { className: "wvs__filesize" }, formatBytes(clip.sizeBytes)),
                  ),
                  h("button", {
                    className: "wvs__clip-play",
                    type: "button",
                    onClick: () => props.onPlay(clip),
                    "aria-label": `Prévisualiser le clip ${clip.name}`,
                  }, "Prévisualiser"),
                ),
              ))
            : h("p", { className: "wvs__empty" }, "Aucun clip rendu associé à cette rediffusion. Les repères enregistrés ne sont pas des fichiers vidéo."),
      props.clipsTruncated
        ? h("p", { className: "wvs__clip-warning", role: "status" }, "La liste Finish est volumineuse; les clips associés affichés peuvent être incomplets. Actualise l’inventaire et vérifie le dossier Finish au besoin.")
        : null,
    );
  }

  function VideoPlayer(props) {
    const [source, setSource] = useState("");
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");

    useEffect(() => {
      if (!props.video) {
        setSource("");
        setLoading(false);
        setError("");
        return undefined;
      }

      const controller = new AbortController();
      let objectUrl = "";
      setSource("");
      setError("");
      setLoading(true);

      async function loadVideo() {
        try {
          const videoCollection = props.video.folderId === "references" ? "references" : "finish";
          const url = `${API_URL}/${videoCollection}/${encodeURIComponent(props.video.name)}`;
          const response = await SDK.authedFetch(url, { signal: controller.signal });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const videoBlob = await response.blob();
          if (controller.signal.aborted) return;
          objectUrl = URL.createObjectURL(videoBlob);
          setSource(objectUrl);
        } catch (_error) {
          if (!controller.signal.aborted) {
            setError("La vidéo n’a pas pu être chargée. Fermez le lecteur et réessayez.");
          }
        } finally {
          if (!controller.signal.aborted) setLoading(false);
        }
      }

      loadVideo();
      return () => {
        controller.abort();
        if (objectUrl) URL.revokeObjectURL(objectUrl);
      };
    }, [props.video]);

    useEffect(() => {
      if (!props.video) return undefined;
      const dialogId = "video-agent-player-dialog";
      const dialog = document.getElementById(dialogId);
      const previousFocus = document.activeElement;
      if (dialog) dialog.focus();

      function handleKeyDown(event) {
        if (event.key === "Escape") {
          props.onClose();
          return;
        }
        if (event.key !== "Tab" || !dialog) return;
        const focusable = Array.from(dialog.querySelectorAll(
          "button:not(:disabled), a[href], input:not(:disabled), textarea:not(:disabled), select:not(:disabled), video[controls], [tabindex]:not([tabindex='-1'])",
        ));
        if (focusable.length === 0) {
          event.preventDefault();
          dialog.focus();
          return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog)) {
          event.preventDefault();
          first.focus();
        }
      }

      window.addEventListener("keydown", handleKeyDown);
      return () => {
        window.removeEventListener("keydown", handleKeyDown);
        if (previousFocus && typeof previousFocus.focus === "function") previousFocus.focus();
      };
    }, [props.video, props.onClose]);

    if (!props.video) return null;
    const titleId = "video-agent-player-title";

    return h(
      "div",
      { className: "wvs__overlay", role: "presentation", onClick: props.onClose },
      h(
        "section",
        {
          className: "wvs__dialog",
          id: "video-agent-player-dialog",
          tabIndex: -1,
          role: "dialog",
          "aria-modal": "true",
          "aria-labelledby": titleId,
          onClick: (event) => event.stopPropagation(),
        },
        h(
          "header",
          { className: "wvs__dialog-header" },
          h("div", { className: "wvs__dialog-heading" },
            h("p", { className: "wvs__eyebrow" }, props.video.folderId === "references" ? "RÉFÉRENCE LOCALE · PRÉVISUALISATION" : "FINISH · PRÉVISUALISATION"),
            h("h2", { className: "wvs__dialog-title", id: titleId }, props.video.name),
          ),
          h("button", { className: "wvs__close", type: "button", onClick: props.onClose, "aria-label": "Fermer le lecteur" }, "Fermer"),
        ),
        loading
          ? h("p", { className: "wvs__player-status", role: "status" }, "Chargement de la vidéo dans le navigateur…")
          : null,
        error ? h("p", { className: "wvs__player-error", role: "alert" }, error) : null,
        source
          ? h("video", {
              className: "wvs__video",
              src: source,
              controls: true,
              autoPlay: true,
              playsInline: true,
              preload: "metadata",
              "aria-label": `Lecteur vidéo : ${props.video.name}`,
            })
          : null,
        h("p", { className: "wvs__player-note" }, "Le fichier sélectionné est chargé dans le navigateur pour la lecture; les MP4 originaux ne sont pas modifiés."),
      ),
    );
  }

  function SkillSection(props) {
    const [name, setName] = useState("");
    const [description, setDescription] = useState("");
    const [instructions, setInstructions] = useState("");
    const [saving, setSaving] = useState(false);
    const [notice, setNotice] = useState(null);

    async function submit(event) {
      event.preventDefault();
      setSaving(true);
      setNotice(null);
      try {
        await props.onCreate({ name: name.trim(), description: description.trim(), instructions: instructions.trim() });
        setName("");
        setDescription("");
        setInstructions("");
        setNotice({ kind: "success", text: "Skill ajouté au plugin. Il sera chargé par Hermes après le prochain redémarrage du gateway." });
      } catch (_error) {
        setNotice({ kind: "error", text: "Le skill n’a pas été créé. Vérifiez le nom et qu’il n’existe pas déjà." });
      } finally {
        setSaving(false);
      }
    }

    let skillsContent;
    if (props.skillsLoading) {
      skillsContent = h("p", { className: "wvs__empty", role: "status" }, "Chargement des skills…");
    } else if (props.skillsError) {
      skillsContent = h("p", { className: "wvs__empty", role: "status" }, "La liste des skills n’a pas pu être chargée; vous pouvez quand même en ajouter un.");
    } else if (props.skills.length === 0) {
      skillsContent = h("p", { className: "wvs__empty" }, "Aucun skill personnalisé pour l’instant.");
    } else {
      skillsContent = h(
        "ul",
        { className: "wvs__skill-list", "aria-label": "Skills personnalisés du plugin" },
        props.skills.map((skill) =>
          h("li", { className: "wvs__skill-item", key: skill.name },
            h("strong", null, skill.name),
            h("span", null, skill.description),
          ),
        ),
      );
    }

    return h(
      "section",
      { className: "wvs__panel wvs__skills", "aria-labelledby": "video-agent-skills-title" },
      h(
        "header",
        { className: "wvs__panel-header" },
        h("div", null,
          h("h2", { className: "wvs__panel-title", id: "video-agent-skills-title" }, "Skills personnalisés"),
          h("p", { className: "wvs__panel-subtitle" }, "Ajoutez des instructions réutilisables à Agent Editor."),
        ),
        h("span", { className: "wvs__count" }, `${props.skills.length} skill${props.skills.length === 1 ? "" : "s"}`),
      ),
      h("div", { className: "wvs__skills-content" },
        skillsContent,
        h("form", { className: "wvs__skill-form", onSubmit: submit },
          h("div", { className: "wvs__field" },
            h("label", { htmlFor: "wvs-skill-name" }, "Nom technique"),
            h("input", {
              id: "wvs-skill-name",
              name: "name",
              type: "text",
              value: name,
              onChange: (event) => setName(event.target.value),
              pattern: "[a-z0-9]+(?:-[a-z0-9]+)*",
              maxLength: 64,
              placeholder: "ex. clip-review",
              required: true,
              autoComplete: "off",
            }),
            h("small", null, "Minuscules, chiffres et tirets; ce nom ne pourra pas être réutilisé."),
          ),
          h("div", { className: "wvs__field" },
            h("label", { htmlFor: "wvs-skill-description" }, "Description"),
            h("input", {
              id: "wvs-skill-description",
              name: "description",
              type: "text",
              value: description,
              onChange: (event) => setDescription(event.target.value),
              maxLength: 1024,
              placeholder: "Quand Hermes devrait utiliser ce skill",
              required: true,
            }),
          ),
          h("div", { className: "wvs__field wvs__field--full" },
            h("label", { htmlFor: "wvs-skill-instructions" }, "Instructions"),
            h("textarea", {
              id: "wvs-skill-instructions",
              name: "instructions",
              value: instructions,
              onChange: (event) => setInstructions(event.target.value),
              maxLength: 32000,
              rows: 8,
              placeholder: "Décris les étapes, contraintes et critères de réussite…",
              required: true,
            }),
          ),
          h("div", { className: "wvs__skill-actions" },
            h("p", { className: notice ? `wvs__skill-notice wvs__skill-notice--${notice.kind}` : "wvs__skill-notice", role: notice ? (notice.kind === "error" ? "alert" : "status") : undefined, "aria-live": "polite" }, notice ? notice.text : "Le skill sera enregistré dans le dossier du plugin et restera portable avec lui."),
            h("button", { className: "wvs__refresh", type: "submit", disabled: saving }, saving ? "Enregistrement…" : "Ajouter le skill"),
          ),
        ),
      ),
    );
  }

  function ReferenceSection() {
    const [references, setReferences] = useState([]);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState(false);
    const [url, setUrl] = useState("");
    const [note, setNote] = useState("");
    const [saving, setSaving] = useState(false);
    const [removingId, setRemovingId] = useState("");
    const [retryingId, setRetryingId] = useState("");
    const [notice, setNotice] = useState(null);

    const loadReferences = useCallback(async (showLoading = true) => {
      if (showLoading) {
        setLoading(true);
        setLoadError(false);
      }
      try {
        const response = await SDK.fetchJSON(REFERENCES_URL);
        if (!response || !Array.isArray(response.references)) throw new Error("Invalid references response");
        setReferences(response.references);
      } catch (_error) {
        if (showLoading) setLoadError(true);
      } finally {
        setLoading(false);
      }
    }, []);

    useEffect(() => {
      loadReferences();
    }, [loadReferences]);

    useEffect(() => {
      if (!references.some((reference) => ["queued", "downloading"].includes(reference.downloadStatus))) return undefined;
      const intervalId = window.setInterval(() => loadReferences(false), 3000);
      return () => window.clearInterval(intervalId);
    }, [references, loadReferences]);

    async function submit(event) {
      event.preventDefault();
      setSaving(true);
      setNotice(null);
      try {
        const response = await SDK.fetchJSON(REFERENCES_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: url.trim(), note: note.trim() }),
        });
        if (!response || !response.reference) throw new Error("Invalid reference response");
        setReferences((current) => [...current, response.reference]);
        setUrl("");
        setNote("");
        setNotice({ kind: "success", text: "Lien ajouté. La vidéo se télécharge en arrière-plan dans References." });
      } catch (_error) {
        setNotice({ kind: "error", text: "Lien refusé ou impossible à enregistrer. Utilisez une URL HTTPS de YouTube ou TikTok." });
      } finally {
        setSaving(false);
      }
    }

    async function retryReference(reference) {
      setRetryingId(reference.id);
      setNotice(null);
      try {
        const response = await SDK.fetchJSON(`${REFERENCES_URL}/${encodeURIComponent(reference.id)}/download`, {
          method: "POST",
        });
        if (!response || !response.reference) throw new Error("Invalid download response");
        setReferences((current) => current.map((item) => item.id === reference.id ? response.reference : item));
        setNotice({ kind: "success", text: "Téléchargement remis en file d’attente." });
      } catch (_error) {
        setNotice({ kind: "error", text: "Impossible de démarrer le téléchargement; réessayez." });
      } finally {
        setRetryingId("");
      }
    }

    async function removeReference(reference) {
      setRemovingId(reference.id);
      setNotice(null);
      try {
        const response = await SDK.fetchJSON(`${REFERENCES_URL}/${encodeURIComponent(reference.id)}`, {
          method: "DELETE",
        });
        if (!response || !response.removed) throw new Error("Invalid delete response");
        setReferences((current) => current.filter((item) => item.id !== reference.id));
        setNotice({ kind: "success", text: "Référence retirée." });
      } catch (_error) {
        setNotice({ kind: "error", text: "Impossible de retirer cette référence; réessayez." });
      } finally {
        setRemovingId("");
      }
    }

    let listContent;
    if (loading) {
      listContent = h("p", { className: "wvs__empty", role: "status" }, "Chargement des références…");
    } else if (loadError) {
      listContent = h("p", { className: "wvs__empty", role: "alert" }, "Les références n’ont pas pu être chargées. Vous pouvez quand même en ajouter.");
    } else if (references.length === 0) {
      listContent = h("p", { className: "wvs__empty", role: "status" }, "Aucun lien enregistré. Ajoutez une vidéo qui illustre le style recherché.");
    } else {
      listContent = h(
        "ul",
        { className: "wvs__reference-list", "aria-label": "Liens de référence pour les shorts" },
        references.map((reference) =>
          h("li", { className: "wvs__reference-item", key: reference.id },
            h("div", { className: "wvs__reference-details" },
              h("span", { className: "wvs__reference-platform" }, reference.platform),
              h("a", {
                className: "wvs__reference-link",
                href: reference.url,
                target: "_blank",
                rel: "noopener noreferrer",
              }, reference.url),
              reference.note ? h("p", { className: "wvs__reference-note" }, reference.note) : null,
              h("p", {
                className: "wvs__reference-note",
                role: ["queued", "downloading"].includes(reference.downloadStatus) ? "status" : undefined,
              }, reference.downloadStatus === "queued" ? "Téléchargement en attente…"
                : reference.downloadStatus === "downloading" ? "Téléchargement en cours…"
                  : reference.downloadStatus === "completed" ? `Copie locale prête · ${reference.videoFile || "MP4 dans References"}`
                    : reference.downloadStatus === "failed" ? (reference.downloadError || "Téléchargement échoué.")
                      : "Copie locale non téléchargée."),
              ["failed", "not_requested", undefined].includes(reference.downloadStatus)
                ? h("button", {
                  className: "wvs__remove",
                  type: "button",
                  onClick: () => retryReference(reference),
                  disabled: retryingId === reference.id,
                }, retryingId === reference.id ? "Mise en file…" : reference.downloadStatus === "failed" ? "Réessayer" : "Télécharger")
                : null,
              h("details", { className: "wvs__reference-observations" },
                h("summary", null, "Points à observer à la création"),
                h("ul", { className: "wvs__observation-list" },
                  (Array.isArray(reference.observationChecklist) ? reference.observationChecklist : []).map((item) =>
                    h("li", { key: item.id }, item.label),
                  ),
                ),
              ),
            ),
            h("button", {
              className: "wvs__remove",
              type: "button",
              onClick: () => {
                const message = reference.videoFile ? "Retirer la référence et supprimer sa copie locale?" : "Retirer cette référence?";
                if (window.confirm && !window.confirm(message)) return;
                removeReference(reference);
              },
              disabled: removingId === reference.id,
              "aria-label": `Retirer le lien ${reference.platform}`,
            }, removingId === reference.id ? "Retrait…" : "Retirer"),
          ),
        ),
      );
    }

    return h(
      "section",
      { className: "wvs__panel wvs__references", "aria-labelledby": "video-agent-references-title" },
      h("header", { className: "wvs__panel-header" },
        h("div", null,
          h("h2", { className: "wvs__panel-title", id: "video-agent-references-title" }, "Références pour les shorts"),
          h("p", { className: "wvs__panel-subtitle" }, "Collez des liens TikTok ou YouTube qui montrent le style recherché."),
        ),
        h("span", { className: "wvs__count" }, `${references.length} référence${references.length === 1 ? "" : "s"}`),
      ),
      h("div", { className: "wvs__references-content" },
        listContent,
        h("form", { className: "wvs__reference-form", onSubmit: submit },
          h("div", { className: "wvs__field wvs__field--full" },
            h("label", { htmlFor: "wvs-reference-url" }, "Lien vidéo"),
            h("input", {
              id: "wvs-reference-url",
              name: "url",
              type: "url",
              value: url,
              onChange: (event) => setUrl(event.target.value),
              maxLength: 2048,
              placeholder: "https://youtu.be/… ou https://www.tiktok.com/@…/video/…",
              required: true,
              autoComplete: "url",
            }),
          ),
          h("div", { className: "wvs__field wvs__field--full" },
            h("label", { htmlFor: "wvs-reference-note" }, "Quoi observer? (facultatif)"),
            h("textarea", {
              id: "wvs-reference-note",
              name: "note",
              value: note,
              onChange: (event) => setNote(event.target.value),
              maxLength: 2000,
              rows: 3,
              placeholder: "Ex. accroche, rythme, sous-titres ou transitions à prendre comme inspiration.",
            }),
          ),
          h("div", { className: "wvs__reference-actions" },
            h("p", { className: notice ? `wvs__skill-notice wvs__skill-notice--${notice.kind}` : "wvs__skill-notice", role: notice ? (notice.kind === "error" ? "alert" : "status") : "note", "aria-live": "polite" }, notice ? notice.text : "Le lien et la note sont conservés localement. Le plugin ne télécharge pas la vidéo."),
            h("button", { className: "wvs__refresh", type: "submit", disabled: saving }, saving ? "Enregistrement…" : "Ajouter le lien"),
          ),
        ),
      ),
    );
  }

  function WaitingVideoPlayer(props) {
    const [error, setError] = useState("");
    const assetLocation = waitingStudioAssetLocation();
    const mediaUrl = waitingVideoApiUrl(props.video.name, assetLocation ? assetLocation.profile : "");
    const sessionProtected = window.__HERMES_AUTH_REQUIRED__ === true;
    const hasSessionToken = typeof window.__HERMES_SESSION_TOKEN__ === "string" && window.__HERMES_SESSION_TOKEN__.length > 0;
    const canUseTokenBridge = !sessionProtected
      && hasSessionToken
      && window.isSecureContext === true
      && window.navigator
      && window.navigator.serviceWorker
      && assetLocation;

    if (!sessionProtected && !canUseTokenBridge) {
      return h("div", { className: "wvs__stream-unavailable", role: "note" },
        h("strong", null, "Lecture progressive indisponible dans ce contexte de navigateur."),
        h("p", null, "Pour lire cette grosse rediffusion sans la télécharger au complet, le navigateur doit permettre un lecteur authentifié. Ouvre le Dashboard sur localhost ou en HTTPS et vérifie que le jeton de session est disponible. Aucun MP4 complet ne sera chargé en remplacement."),
      );
    }

    const mediaPlayer = sessionProtected
      ? h("video", {
          ref: props.videoRef,
          className: "wvs__waiting-video",
          src: mediaUrl,
          controls: true,
          playsInline: true,
          preload: "metadata",
          onTimeUpdate: props.onTimeUpdate,
          onLoadedMetadata: (event) => {
            props.onTimeUpdate(event);
            props.onPlayerReady(Number.isFinite(event.currentTarget.duration));
          },
          onError: () => setError("Impossible de lire la rediffusion. Vérifie que ta session Dashboard est encore active et que le MP4 est accessible."),
          "aria-label": `Lecture de la rediffusion ${props.video.name}`,
        })
      : h("iframe", {
          ref: props.videoRef,
          className: "wvs__waiting-video",
          src: `${assetLocation.assetRoot}media-player.html`,
          title: `Lecture de la rediffusion ${props.video.name}`,
          allow: "autoplay; fullscreen",
          referrerPolicy: "no-referrer",
          onLoad: () => {
            const frame = props.videoRef.current;
            if (!frame || !frame.contentWindow) return;
            const streamUrl = `${assetLocation.assetRoot}stream/${encodeURIComponent(props.video.name)}`;
            frame.contentWindow.postMessage({
              type: "video-agent:init-media-player",
              token: window.__HERMES_SESSION_TOKEN__,
              filename: props.video.name,
              apiUrl: new URL(mediaUrl, window.location.origin).href,
              streamUrl: new URL(streamUrl, window.location.origin).href,
            }, window.location.origin);
          },
          onError: () => setError("Impossible de démarrer le lecteur authentifié. Vérifie que le Dashboard est ouvert sur localhost ou en HTTPS."),
        });

    return h("div", { className: "wvs__video-player" },
      mediaPlayer,
      error ? h("p", { className: "wvs__video-error", role: "alert" }, error) : null,
      h("p", { className: "wvs__reference-hint" }, "Lecture progressive par plages d’octets; le MP4 source reste en lecture seule."),
    );
  }

  function ContentWorkspace(props) {
    const [workspace, setWorkspace] = useState({ loading: true, error: "", items: [], moments: [], clips: [], clipsTruncated: false, references: [], observationChecklist: [] });
    const [draft, setDraft] = useState({ format: "short", title: "", concept: "", hook: "", script: "", notes: "" });
    const [saving, setSaving] = useState(false);
    const [notice, setNotice] = useState(null);
    const [currentTime, setCurrentTime] = useState(0);
    const [playerReady, setPlayerReady] = useState(false);
    const [momentNote, setMomentNote] = useState("");
    const [momentTarget, setMomentTarget] = useState("both");
    const [savingMoment, setSavingMoment] = useState(false);
    const [momentNotice, setMomentNotice] = useState(null);
    const videoRef = useRef(null);
    const assetLocation = waitingStudioAssetLocation();
    const sessionProtected = window.__HERMES_AUTH_REQUIRED__ === true;
    const previewAvailable = sessionProtected || (
      typeof window.__HERMES_SESSION_TOKEN__ === "string"
      && window.__HERMES_SESSION_TOKEN__.length > 0
      && window.isSecureContext === true
      && window.navigator
      && window.navigator.serviceWorker
      && assetLocation
    );
    const workspaceUrl = `${CONTENT_URL}/${encodeURIComponent(props.video.name)}`;

    const loadWorkspace = useCallback(async () => {
      setWorkspace((current) => ({ ...current, loading: true, error: "" }));
      try {
        const response = await SDK.fetchJSON(workspaceUrl);
        if (!response || !Array.isArray(response.items) || !Array.isArray(response.references)) {
          throw new Error("Invalid content workspace response");
        }
        setWorkspace({
          loading: false,
          error: "",
          items: response.items,
          moments: Array.isArray(response.moments) ? response.moments : [],
          clips: Array.isArray(response.clips) ? response.clips : [],
          clipsTruncated: response.clipsTruncated === true,
          references: response.references,
          observationChecklist: Array.isArray(response.observationChecklist) ? response.observationChecklist : [],
        });
      } catch (_error) {
        setWorkspace((current) => ({ ...current, loading: false, error: "Impossible de charger les contenus et références de cette rediffusion." }));
      }
    }, [workspaceUrl]);

    useEffect(() => {
      loadWorkspace();
    }, [loadWorkspace]);

    const handleMediaMessage = useCallback((event) => {
      const frame = videoRef.current;
      if (event.origin !== window.location.origin || !frame || !frame.contentWindow || event.source !== frame.contentWindow) return;
      const data = event.data;
      if (!data || typeof data !== "object") return;
      if (data.type === "video-agent:player-ready") {
        setPlayerReady(Number.isFinite(data.duration) && data.duration > 0);
      } else if (data.type === "video-agent:time-update") {
        if (Number.isFinite(data.currentTime)) setCurrentTime(data.currentTime);
        if (Number.isFinite(data.duration)) setPlayerReady(data.duration > 0);
      } else if (data.type === "video-agent:player-error") {
        setPlayerReady(false);
        setMomentNotice({ kind: "error", text: "La lecture progressive a échoué. Vérifie la session du Dashboard et réessaie." });
      }
    }, []);

    useEffect(() => {
      window.addEventListener("message", handleMediaMessage);
      return () => window.removeEventListener("message", handleMediaMessage);
    }, [handleMediaMessage]);

    const updateDraft = (field, value) => {
      setDraft((current) => ({ ...current, [field]: value }));
    };

    const saveMoment = async (event) => {
      event.preventDefault();
      if (!previewAvailable || !playerReady || !Number.isFinite(currentTime)) {
        setMomentNotice({ kind: "error", text: "Attends que le lecteur soit prêt, puis place-toi sur le moment à repérer." });
        return;
      }
      setSavingMoment(true);
      setMomentNotice(null);
      try {
        const timeSeconds = Math.round(currentTime * 1000) / 1000;
        const response = await SDK.fetchJSON(`${workspaceUrl}/moments`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ timeSeconds, note: momentNote, targetFormat: momentTarget }),
        });
        if (!response || !response.moment) throw new Error("Invalid moment response");
        setWorkspace((current) => ({ ...current, moments: [...current.moments, response.moment] }));
        setMomentNote("");
        setMomentNotice({ kind: "success", text: `Repère ${formatTimecode(timeSeconds)} enregistré.` });
      } catch (_error) {
        setMomentNotice({ kind: "error", text: "Impossible d’enregistrer ce repère. Vérifie la note et réessaie." });
      } finally {
        setSavingMoment(false);
      }
    };

    const addMomentToDraft = (moment) => {
      const line = `${formatTimecode(moment.timeSeconds)} · ${momentTargetLabel(moment.targetFormat)} — ${moment.note}`;
      const nextNotes = draft.notes ? `${draft.notes}${String.fromCharCode(10)}${line}` : line;
      if (nextNotes.length > 4000) {
        setNotice({ kind: "error", text: "Les notes du brouillon sont pleines; raccourcis-les avant d’ajouter ce repère." });
        return;
      }
      updateDraft("notes", nextNotes);
      setNotice({ kind: "success", text: `Repère ${formatTimecode(moment.timeSeconds)} ajouté aux notes du brouillon. Enregistre le brouillon pour le conserver.` });
    };

    const seekToMoment = (moment) => {
      const player = videoRef.current;
      if (!player) return;
      if (typeof player.currentTime === "number") {
        player.currentTime = moment.timeSeconds;
        const playback = typeof player.play === "function" ? player.play() : null;
        if (playback && typeof playback.catch === "function") playback.catch(() => {});
      } else if (player.contentWindow) {
        player.contentWindow.postMessage({ type: "video-agent:seek", timeSeconds: moment.timeSeconds }, window.location.origin);
      }
      setCurrentTime(moment.timeSeconds);
    };

    const saveDraft = async (event) => {
      event.preventDefault();
      setSaving(true);
      setNotice(null);
      try {
        const response = await SDK.fetchJSON(`${CONTENT_URL}/${encodeURIComponent(props.video.name)}/items`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(draft),
        });
        if (!response || !response.item) throw new Error("Invalid content draft response");
        setWorkspace((current) => ({ ...current, items: [...current.items, response.item] }));
        setDraft((current) => ({ ...current, title: "", concept: "", hook: "", script: "", notes: "" }));
        setNotice({ kind: "success", text: "Brouillon enregistré avec un instantané des références et des points à observer." });
      } catch (_error) {
        setNotice({ kind: "error", text: "Impossible d’enregistrer le brouillon. Vérifiez les champs et réessayez." });
      } finally {
        setSaving(false);
      }
    };

    const references = Array.isArray(workspace.references) ? workspace.references : [];
    const checklist = Array.isArray(workspace.observationChecklist) ? workspace.observationChecklist : [];
    const items = Array.isArray(workspace.items) ? workspace.items : [];
    const moments = Array.isArray(workspace.moments) ? workspace.moments : [];
    const clips = Array.isArray(workspace.clips) ? workspace.clips : [];
    const observationList = (entries, prefix) => h("ul", { className: "wvs__observation-list" },
      entries.map((entry) => h("li", { key: `${prefix}-${entry.id}` }, entry.label)),
    );

    return h("main", { className: "wvs__workspace", "aria-labelledby": "wvs-content-title" },
      h("button", {
        className: "wvs__refresh wvs__content-back",
        type: "button",
        onClick: props.onBack,
      }, "← Retour aux rediffusions"),
      h("header", { className: "wvs__content-heading" },
        h("div", null,
          h("p", { className: "wvs__eyebrow" }, "Agent Editor · espace de contenu"),
          h("h1", { id: "wvs-content-title" }, props.video.name),
          h("p", { className: "wvs__panel-subtitle" }, "Brouillons liés à cette rediffusion. La vidéo source reste intacte."),
        ),
      ),
      h(FinishedClipsSection, {
        clips,
        clipsTruncated: workspace.clipsTruncated === true,
        loading: workspace.loading,
        error: workspace.error,
        onPlay: props.onPlayClip,
      }),
      h("section", { className: "wvs__panel wvs__moment-workspace", "aria-labelledby": "wvs-moment-title" },
        h("div", { className: "wvs__panel-header" },
          h("div", null,
            h("h2", { className: "wvs__panel-title", id: "wvs-moment-title" }, "Visionner et repérer des moments"),
            h("p", { className: "wvs__panel-subtitle" }, "Utilise la timeline du lecteur, puis enregistre un timecode et une note pour le Short ou la vidéo."),
          ),
        ),
        h("div", { className: "wvs__moment-layout" },
          h(WaitingVideoPlayer, {
            key: props.video.name,
            video: props.video,
            videoRef,
            onTimeUpdate: (event) => {
              const position = event && event.currentTarget ? event.currentTarget.currentTime : NaN;
              if (Number.isFinite(position)) setCurrentTime(position);
            },
            onPlayerReady: setPlayerReady,
          }),
          h("div", { className: "wvs__moment-sidebar" },
            h("p", { className: "wvs__current-time" },
              h("span", null, "Position actuelle"),
              h("time", { dateTime: `PT${Math.floor(currentTime)}S` }, formatTimecode(currentTime)),
            ),
            h("form", { className: "wvs__moment-form", onSubmit: saveMoment },
              h("div", { className: "wvs__field wvs__field--full" },
                h("label", { htmlFor: "wvs-moment-note" }, "Quoi retenir de ce moment?"),
                h("textarea", {
                  id: "wvs-moment-note",
                  value: momentNote,
                  onChange: (event) => setMomentNote(event.target.value),
                  maxLength: 500,
                  rows: 3,
                  required: true,
                  placeholder: "Ex. élimination inattendue, réaction, punchline à vérifier à l’écoute…",
                }),
              ),
              h("div", { className: "wvs__field wvs__field--full" },
                h("label", { htmlFor: "wvs-moment-target" }, "À intégrer dans"),
                h("select", { id: "wvs-moment-target", value: momentTarget, onChange: (event) => setMomentTarget(event.target.value) },
                  h("option", { value: "short" }, "Short"),
                  h("option", { value: "video" }, "Vidéo"),
                  h("option", { value: "both" }, "Les deux"),
                ),
              ),
              h("div", { className: "wvs__reference-actions" },
                h("p", { className: momentNotice ? `wvs__skill-notice wvs__skill-notice--${momentNotice.kind}` : "wvs__skill-notice", role: momentNotice ? (momentNotice.kind === "error" ? "alert" : "status") : "note", "aria-live": "polite" }, momentNotice ? momentNotice.text : "Les repères sont sauvegardés séparément, pour cette rediffusion."),
                h("button", { className: "wvs__refresh", type: "submit", disabled: savingMoment || !previewAvailable || !playerReady }, savingMoment ? "Enregistrement…" : "Marquer ce moment"),
              ),
            ),
            h("div", { className: "wvs__moment-list-section" },
              h("h3", { className: "wvs__content-subheading" }, `Moments repérés · ${moments.length}`),
              moments.length ? h("ul", { className: "wvs__moment-list" }, moments.map((moment) =>
                h("li", { className: "wvs__moment-item", key: moment.id },
                  h("button", { className: "wvs__moment-seek", type: "button", onClick: () => seekToMoment(moment), title: `Reprendre à ${formatTimecode(moment.timeSeconds)}` }, formatTimecode(moment.timeSeconds)),
                  h("div", { className: "wvs__moment-details" },
                    h("span", { className: "wvs__content-format" }, momentTargetLabel(moment.targetFormat)),
                    h("p", null, moment.note),
                    h("button", { className: "wvs__moment-add", type: "button", onClick: () => addMomentToDraft(moment) }, "Ajouter aux notes du brouillon"),
                  ),
                ),
              )) : h("p", { className: "wvs__empty" }, "Aucun moment repéré pour cette rediffusion."),
            ),
          ),
        ),
      ),
      workspace.loading ? h("p", { className: "wvs__status", role: "status" }, "Chargement de l’espace de contenu…") : null,
      workspace.error ? h("div", { className: "wvs__error", role: "alert" },
        h("p", null, workspace.error),
        h("button", { className: "wvs__refresh", type: "button", onClick: loadWorkspace }, "Réessayer"),
      ) : null,
      h("div", { className: "wvs__workspace-grid" },
        h("section", { className: "wvs__panel", "aria-labelledby": "wvs-content-references" },
          h("div", { className: "wvs__panel-header" },
            h("div", null,
              h("h2", { className: "wvs__panel-title", id: "wvs-content-references" }, "Références et repères"),
              h("p", { className: "wvs__panel-subtitle" }, "Les liens et consignes actifs au moment de la création seront copiés dans chaque brouillon."),
            ),
            h("span", { className: "wvs__count" }, pluralizeVideo(references.length)),
          ),
          references.length ? h("ul", { className: "wvs__content-reference-list" }, references.map((reference) =>
            h("li", { className: "wvs__content-reference", key: reference.id },
              h("a", { href: reference.url, target: "_blank", rel: "noopener noreferrer" }, `${reference.platform} · ${reference.url}`),
              reference.note ? h("p", { className: "wvs__reference-note" }, reference.note) : null,
              h("details", { className: "wvs__reference-observations" },
                h("summary", null, "Points à observer"),
                observationList(Array.isArray(reference.observationChecklist) ? reference.observationChecklist : [], reference.id),
              ),
            ),
          )) : h("p", { className: "wvs__empty" }, "Aucune référence enregistrée. Les repères standards restent disponibles pour ce brouillon."),
          h("h3", { className: "wvs__content-subheading" }, "Repères standards"),
          observationList(checklist, "standard"),
          h("p", { className: "wvs__reference-hint" }, "Les liens ne sont pas téléchargés. Le brouillon conserve un instantané; il ne constitue pas une analyse automatique de la vidéo."),
        ),
        h("section", { className: "wvs__panel", "aria-labelledby": "wvs-content-new" },
          h("div", { className: "wvs__panel-header" },
            h("div", null,
              h("h2", { className: "wvs__panel-title", id: "wvs-content-new" }, "Créer un brouillon"),
              h("p", { className: "wvs__panel-subtitle" }, "Pour l’instant : idées et textes seulement, sans rendu ni publication."),
            ),
          ),
          h("form", { className: "wvs__content-form", onSubmit: saveDraft },
            h("div", { className: "wvs__field" },
              h("label", { htmlFor: "wvs-content-draft-format" }, "Format"),
              h("select", { id: "wvs-content-draft-format", value: draft.format, onChange: (event) => updateDraft("format", event.target.value) },
                h("option", { value: "short" }, "Short · vertical 9:16"),
                h("option", { value: "video" }, "Vidéo · format long"),
              ),
              draft.format === "short" ? h("p", { className: "wvs__field-hint" }, "Cible actuelle : 1080×1920 · 30 fps · environ 15 s.") : null,
            ),
            h("div", { className: "wvs__field" },
              h("label", { htmlFor: "wvs-content-draft-title" }, "Titre"),
              h("input", { id: "wvs-content-draft-title", value: draft.title, maxLength: 200, required: true, onChange: (event) => updateDraft("title", event.target.value) }),
            ),
            h("div", { className: "wvs__field wvs__field--full" },
              h("label", { htmlFor: "wvs-content-draft-concept" }, "Concept / angle"),
              h("textarea", { id: "wvs-content-draft-concept", value: draft.concept, maxLength: 5000, rows: 3, required: true, onChange: (event) => updateDraft("concept", event.target.value), placeholder: "Quelle histoire ou quel moment de cette rediffusion mettre en valeur?" }),
            ),
            h("div", { className: "wvs__field wvs__field--full" },
              h("label", { htmlFor: "wvs-content-hook" }, "Accroche (facultatif)"),
              h("textarea", { id: "wvs-content-hook", value: draft.hook, maxLength: 2000, rows: 2, onChange: (event) => updateDraft("hook", event.target.value) }),
            ),
            h("div", { className: "wvs__field wvs__field--full" },
              h("label", { htmlFor: "wvs-content-script" }, "Script / déroulement (facultatif)"),
              h("textarea", { id: "wvs-content-script", value: draft.script, maxLength: 20000, rows: 5, onChange: (event) => updateDraft("script", event.target.value), placeholder: "Structure, narration ou étapes à vérifier au montage." }),
            ),
            h("div", { className: "wvs__field wvs__field--full" },
              h("label", { htmlFor: "wvs-content-notes" }, "Notes de montage (facultatif)"),
              h("textarea", { id: "wvs-content-notes", value: draft.notes, maxLength: 4000, rows: 3, onChange: (event) => updateDraft("notes", event.target.value) }),
            ),
            h("div", { className: "wvs__reference-actions wvs__field--full" },
              h("p", { className: notice ? `wvs__skill-notice wvs__skill-notice--${notice.kind}` : "wvs__skill-notice", role: notice ? (notice.kind === "error" ? "alert" : "status") : "note", "aria-live": "polite" }, notice ? notice.text : "Chaque brouillon sauvegarde une copie des références et de leurs points d’observation."),
              h("button", { className: "wvs__refresh", type: "submit", disabled: saving || workspace.loading }, saving ? "Enregistrement…" : "Enregistrer le brouillon"),
            ),
          ),
        ),
      ),
      h("section", { className: "wvs__panel wvs__content-drafts", "aria-labelledby": "wvs-content-items" },
        h("div", { className: "wvs__panel-header" },
          h("div", null,
            h("h2", { className: "wvs__panel-title", id: "wvs-content-items" }, "Contenus enregistrés"),
            h("p", { className: "wvs__panel-subtitle" }, "Les brouillons restent rattachés au nom du MP4 de Waiting."),
          ),
          h("span", { className: "wvs__count" }, pluralizeVideo(items.length)),
        ),
        items.length ? h("div", { className: "wvs__content-draft-list" }, items.map((item) =>
          h("article", { className: "wvs__content-draft", key: item.id },
            h("div", { className: "wvs__content-draft-heading" },
              h("h3", null, item.title),
              h("span", { className: "wvs__content-format" }, item.format === "short" ? "Short" : "Vidéo"),
            ),
            h("p", { className: "wvs__content-date" }, item.createdAt ? new Date(item.createdAt).toLocaleString() : "Date inconnue"),
            h("p", { className: "wvs__content-concept" }, item.concept),
            item.hook ? h("p", null, h("strong", null, "Accroche : "), item.hook) : null,
            item.script ? h("div", { className: "wvs__content-text" }, h("h4", null, "Script / déroulement"), h("p", null, item.script)) : null,
            item.notes ? h("div", { className: "wvs__content-text" }, h("h4", null, "Notes de montage"), h("p", null, item.notes)) : null,
            h("details", { className: "wvs__content-snapshot" },
              h("summary", null, `Références et repères sauvegardés (${(item.creationContext?.references || []).length})`),
              (item.creationContext?.references || []).length ? h("ul", { className: "wvs__content-reference-list" }, item.creationContext.references.map((reference) =>
                h("li", { className: "wvs__content-reference", key: reference.id },
                  h("a", { href: reference.url, target: "_blank", rel: "noopener noreferrer" }, `${reference.platform} · ${reference.url}`),
                  reference.note ? h("p", { className: "wvs__reference-note" }, reference.note) : null,
                  h("details", { className: "wvs__reference-observations" },
                    h("summary", null, "Points à observer"),
                    observationList(Array.isArray(reference.observationChecklist) ? reference.observationChecklist : [], reference.id),
                  ),
                ),
              )) : h("p", null, "Aucun lien de référence au moment de la création."),
              h("h4", null, "Repères standards sauvegardés"),
              observationList(item.creationContext?.observationChecklist || [], item.id),
            ),
          ),
        )) : h("p", { className: "wvs__empty" }, "Aucun brouillon pour cette rediffusion. Créez-en un ci-dessus."),
      ),
    );
  }

  function WaitingStudioPage() {
    const [snapshot, setSnapshot] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [updatedAt, setUpdatedAt] = useState(null);
    const [skills, setSkills] = useState([]);
    const [skillsLoading, setSkillsLoading] = useState(true);
    const [skillsError, setSkillsError] = useState(false);
    const [selectedVideo, setSelectedVideo] = useState(null);
    const [selectedContentSource, setSelectedContentSource] = useState(null);

    const refresh = useCallback(async () => {
      setLoading(true);
      setError("");
      try {
        const response = await SDK.fetchJSON(API_URL);
        if (!response || !Array.isArray(response.folders)) throw new Error("Invalid inventory response");
        setSnapshot(response);
        setUpdatedAt(new Date());
      } catch (_error) {
        setError("Impossible de charger les listes. Réessayez dans un instant.");
      } finally {
        setLoading(false);
      }
    }, []);

    const loadSkills = useCallback(async () => {
      setSkillsLoading(true);
      setSkillsError(false);
      try {
        const response = await SDK.fetchJSON(SKILLS_URL);
        if (!response || !Array.isArray(response.skills)) throw new Error("Invalid skills response");
        setSkills(response.skills);
      } catch (_error) {
        setSkillsError(true);
      } finally {
        setSkillsLoading(false);
      }
    }, []);

    const createSkill = useCallback(async (payload) => {
      const response = await SDK.fetchJSON(SKILLS_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response || !response.skill) throw new Error("Invalid skill response");
      setSkills((current) => [...current.filter((skill) => skill.name !== response.skill.name), response.skill].sort((a, b) => a.name.localeCompare(b.name)));
      return response;
    }, []);

    const closePlayer = useCallback(() => setSelectedVideo(null), []);

    useEffect(() => {
      refresh();
      loadSkills();
    }, [refresh, loadSkills]);

    if (selectedContentSource) {
      return h("div", { className: "wvs__workspace-shell" },
        h(ContentWorkspace, {
          key: selectedContentSource.name,
          video: selectedContentSource,
          onBack: () => setSelectedContentSource(null),
          onPlayClip: setSelectedVideo,
        }),
        h(VideoPlayer, { video: selectedVideo, onClose: closePlayer }),
      );
    }

    const folders = snapshot && Array.isArray(snapshot.folders) ? snapshot.folders : [];
    const fallbackFolders = [
      { id: "waiting", label: "Waiting", available: true, count: 0, videos: [] },
      { id: "references", label: "References", available: true, count: 0, videos: [] },
      { id: "finish", label: "Finish", available: true, count: 0, videos: [] },
    ];
    const activeFolders = folders.length ? folders : fallbackFolders;
    const waitingFolder = activeFolders.find((folder) => folder.id === "waiting") || fallbackFolders[0];
    const referenceFolder = activeFolders.find((folder) => folder.id === "references") || (!snapshot ? fallbackFolders[1] : null);
    const finishFolder = activeFolders.find((folder) => folder.id === "finish") || (!snapshot ? fallbackFolders[2] : null);

    return h(
      "div",
      { className: "wvs" },
      h(
        "div",
        { className: "wvs__header" },
        h(
          "div",
          { className: "wvs__heading" },
          h("p", { className: "wvs__eyebrow" }, "BIBLIOTHÈQUE AGENT EDITOR"),
          h("h1", { className: "wvs__title" }, "Mes vidéos"),
          h("p", { className: "wvs__intro" }, "Organise Waiting, tes vidéos locales de référence et Finish depuis un seul workspace. Ouvre une rediffusion de Waiting pour retrouver ses repères et brouillons; les fichiers source restent intacts."),
        ),
        h("button", { className: "wvs__refresh", type: "button", onClick: refresh, disabled: loading, "aria-busy": loading }, loading ? "Actualisation…" : "Actualiser"),
      ),
      h(
        "div",
        { className: "wvs__notice", role: error ? "alert" : "status", "aria-live": "polite" },
        error || (loading ? "Chargement des listes…" : snapshot && snapshot.readOnly
          ? `Vidéos en lecture seule · actualisé à ${updatedAt ? updatedAt.toLocaleTimeString("fr-CA", { hour: "2-digit", minute: "2-digit" }) : ""}`
          : ""),
      ),
      h(
        "div",
        { className: "wvs__grid wvs__grid--primary" },
        h(VideoSection, { folder: waitingFolder, loading, hasSnapshot: Boolean(snapshot), error, onPlay: setSelectedVideo, onOpenContent: setSelectedContentSource }),
      ),
      finishFolder
        ? h("details", { className: "wvs__finish-library" },
            h("summary", null, `Parcourir les autres vidéos dans Finish · ${finishFolder.count}`),
            h(VideoSection, { folder: finishFolder, loading, hasSnapshot: Boolean(snapshot), error, onPlay: setSelectedVideo, onOpenContent: setSelectedContentSource }),
          )
        : null,
      referenceFolder
        ? h("details", { className: "wvs__finish-library wvs__reference-library" },
            h("summary", null, `Parcourir les vidéos de référence locales · ${referenceFolder.count}`),
            h(VideoSection, { folder: referenceFolder, loading, hasSnapshot: Boolean(snapshot), error, onPlay: setSelectedVideo, onOpenContent: setSelectedContentSource }),
          )
        : null,
      h(SkillSection, { skills, skillsLoading, skillsError, onCreate: createSkill }),
      h(VideoPlayer, { video: selectedVideo, onClose: closePlayer }),
      h(ReferenceSection, null),
      h("p", { className: "wvs__footnote" }, "Waiting, References et Finish sont lus depuis le point de montage workspace quand ses trois dossiers sont prêts; sinon l’ancien chemin demeure utilisé. Les liens de référence vont dans References/links.json lorsque le workspace est actif; brouillons et repères restent dans les données d’état locales d’Hermes. Les prévisualisations Finish/References chargent le fichier choisi dans le navigateur; aucun MP4 source n’est modifié. Les skills personnalisés deviennent actifs après le redémarrage du gateway."),
    );
  }

  window.__HERMES_PLUGINS__.register("video-agent", WaitingStudioPage);
})();
