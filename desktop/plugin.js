import { Button, ROUTES_AREA, SIDEBAR_NAV_AREA, useQuery, useMutation, useQueryClient } from '@hermes/plugin-sdk'
import * as React from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const PLUGIN_ID = 'video-agent'
const QUERY_ROOT = ['video-agent']

export function parseTimecode(value) {
  if (typeof value !== 'string') return null
  const parts = value.trim().split(':')
  if (parts.length !== 2 && parts.length !== 3) return null
  const secondsText = parts.pop()
  if (!/^\d{1,2}(?:\.\d{1,3})?$/.test(secondsText)) return null
  const seconds = Number(secondsText)
  if (!Number.isFinite(seconds) || seconds >= 60) return null

  const minuteText = parts.pop()
  if (!/^\d{1,2}$/.test(minuteText)) return null
  const minutes = Number(minuteText)
  if (minutes >= 60 && parts.length === 1) return null

  const hours = parts.length === 1 ? Number(parts[0]) : 0
  if (parts.length === 1 && (!/^\d{1,2}$/.test(parts[0]) || hours > 24)) return null
  const totalSeconds = hours * 3600 + minutes * 60 + seconds
  if (totalSeconds > 86400 || (hours === 24 && (minutes !== 0 || seconds !== 0))) return null
  return Math.round(totalSeconds * 1000) / 1000
}

export function formatTimecode(value) {
  if (!Number.isFinite(value) || value < 0) return '—'
  const totalMilliseconds = Math.round(value * 1000)
  const hours = Math.floor(totalMilliseconds / 3600000)
  const minutes = Math.floor((totalMilliseconds % 3600000) / 60000)
  const seconds = Math.floor((totalMilliseconds % 60000) / 1000)
  const milliseconds = totalMilliseconds % 1000
  return [hours, minutes, seconds].map(part => String(part).padStart(2, '0')).join(':') + `.${String(milliseconds).padStart(3, '0')}`
}

function errorText(error, fallback) {
  return error instanceof Error && error.message ? error.message : fallback
}

function buildHermesRemoteMediaUrl(mediaPath) {
  if (typeof mediaPath !== 'string' || mediaPath.length > 8192 || /[\u0000-\u001f\u007f]/.test(mediaPath)) {
    throw new Error('Le chemin du média est invalide.')
  }
  const isAbsolutePath = mediaPath.startsWith('/') || /^[A-Za-z]:[\\/]/.test(mediaPath) || /^\\\\[^\\]+\\[^\\]+/.test(mediaPath)
  if (!isAbsolutePath || !/\.mp4$/i.test(mediaPath)) {
    throw new Error('Le chemin du média est invalide.')
  }
  return `hermes-media://remote/${encodeURIComponent(mediaPath)}`
}

function formatBytes(value) {
  if (!Number.isFinite(value) || value < 0) return ''
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} Ko`
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} Mo`
  return `${(value / (1024 * 1024 * 1024)).toFixed(2)} Go`
}

function buttonClass(active = false) {
  return [
    'inline-flex min-h-9 items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm transition-colors',
    active
      ? 'border-(--ui-accent) text-(--ui-accent)'
      : 'border-(--ui-stroke-secondary) text-(--ui-text-secondary) hover:bg-(--chrome-action-hover) hover:text-foreground',
  ].join(' ')
}

function fieldClass() {
  return 'w-full rounded-md border border-(--ui-stroke-secondary) bg-transparent px-3 py-2 text-sm text-foreground placeholder:text-(--ui-text-quaternary) focus:outline-none focus:ring-1 focus:ring-(--ui-accent)'
}

function Panel({ title, description, children, className = '' }) {
  return jsxs('section', {
    className: `flex min-h-0 flex-col gap-3 rounded-lg border border-(--ui-stroke-secondary) p-4 ${className}`,
    children: [
      jsxs('div', {
        className: 'flex flex-col gap-1',
        children: [
          jsx('h2', { className: 'text-sm font-semibold text-foreground', children: title }),
          description ? jsx('p', { className: 'text-xs text-(--ui-text-tertiary)', children: description }) : null,
        ],
      }),
      children,
    ],
  })
}

function VideoList({ videos, selectedName, onSelect, search }) {
  const filtered = videos.filter(video => video.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()))
  if (!filtered.length) {
    return jsx('p', {
      className: 'py-8 text-center text-sm text-(--ui-text-tertiary)',
      children: search ? 'Aucune vidéo ne correspond à la recherche.' : 'Aucune vidéo dans ce dossier.',
    })
  }
  return jsx('ul', {
    className: 'flex min-h-0 flex-col gap-1 overflow-auto',
    role: 'list',
    children: filtered.map(video => jsx('li', {
      children: jsxs('button', {
        type: 'button',
        className: `flex w-full flex-col gap-1 rounded-md px-3 py-2 text-left transition-colors ${selectedName === video.name ? 'bg-(--chrome-action-hover) text-foreground' : 'text-(--ui-text-secondary) hover:bg-(--chrome-action-hover)'}`,
        'aria-current': selectedName === video.name ? 'true' : undefined,
        onClick: () => onSelect(video.name),
        children: [
          jsx('span', { className: 'break-all text-sm font-medium', children: video.name }),
          video.sizeBytes !== undefined ? jsx('span', { className: 'text-xs text-(--ui-text-quaternary)', children: formatBytes(video.sizeBytes) }) : null,
        ],
      }),
    }, video.name)),
  })
}

function PlaybackPanel({ rest, folderId, video, onTimeUpdate, seekTime }) {
  const [renewalError, setRenewalError] = React.useState('')
  const [retryGeneration, setRetryGeneration] = React.useState(0)
  const playerRef = React.useRef(null)
  const restoreTimeRef = React.useRef(0)
  const mediaQuery = useQuery({
    queryKey: [...QUERY_ROOT, 'media-source', folderId, video.name],
    queryFn: async () => {
      const source = await rest('/video-media-source', {
        method: 'POST',
        body: { folderId, filename: video.name },
      })
      return { mediaUrl: buildHermesRemoteMediaUrl(source?.mediaPath) }
    },
    enabled: Boolean(video),
    staleTime: 0,
    retry: false,
  })
  const mediaSource = mediaQuery.data

  React.useEffect(() => {
    const player = playerRef.current
    if (player && typeof seekTime === 'number' && player.readyState >= 1) player.currentTime = seekTime
  }, [seekTime, mediaSource?.mediaUrl])

  function retryStream() {
    restoreTimeRef.current = Number(playerRef.current?.currentTime) || 0
    setRenewalError('')
    setRetryGeneration(value => value + 1)
    mediaQuery.refetch()
  }

  if (!mediaSource && mediaQuery.isLoading) {
    return jsx('div', { className: 'flex aspect-video items-center justify-center rounded-md border border-(--ui-stroke-secondary) text-sm text-(--ui-text-tertiary)', role: 'status', children: 'Préparation du flux vidéo…' })
  }
  if (!mediaSource && mediaQuery.error) {
    return jsxs('div', {
      className: 'flex aspect-video flex-col items-center justify-center gap-3 rounded-md border border-(--ui-stroke-secondary) p-4 text-sm text-(--ui-text-secondary)',
      role: 'alert',
      children: [jsx('span', { children: errorText(mediaQuery.error, 'Le flux vidéo n’a pas pu être préparé.') }), jsx(Button, { type: 'button', onClick: () => mediaQuery.refetch(), children: 'Réessayer' })],
    })
  }

  return jsxs('div', {
    className: 'flex flex-col gap-2',
    children: [
      mediaSource?.mediaUrl ? jsx('video', {
        key: `${mediaSource.mediaUrl}:${retryGeneration}`,
        ref: playerRef,
        className: 'aspect-video w-full rounded-md border border-(--ui-stroke-secondary) bg-black/80 object-contain',
        src: mediaSource.mediaUrl,
        controls: true,
        preload: 'metadata',
        playsInline: true,
        referrerPolicy: 'no-referrer',
        'aria-label': `Lecture de ${video.name}`,
        onTimeUpdate: event => onTimeUpdate?.(Number(event.currentTarget.currentTime) || 0),
        onLoadedMetadata: event => {
          const targetTime = restoreTimeRef.current > 0 ? restoreTimeRef.current : seekTime
          if (typeof targetTime === 'number') {
            event.currentTarget.currentTime = targetTime
            restoreTimeRef.current = 0
          }
        },
        onError: () => setRenewalError('La lecture est interrompue. Réessaie le flux.'),
      }) : jsx('div', { className: 'flex aspect-video items-center justify-center rounded-md border border-(--ui-stroke-secondary) text-sm text-(--ui-text-tertiary)', role: 'status', children: 'Le flux vidéo est indisponible.' }),
      jsxs('div', {
        className: 'flex flex-wrap items-center justify-between gap-2 text-xs text-(--ui-text-tertiary)',
        children: [jsx('span', { children: renewalError || 'Lecture progressive · authentification Hermes Desktop' }), jsx(Button, { type: 'button', onClick: retryStream, children: 'Réessayer le flux' })],
      }),
    ],
  })
}

function MomentWorkspace({ rest, video, contentQuery, currentFolderId }) {
  const queryClient = useQueryClient()
  const [timecode, setTimecode] = React.useState('')
  const [note, setNote] = React.useState('')
  const [targetFormat, setTargetFormat] = React.useState('both')
  const [validationError, setValidationError] = React.useState('')
  const [currentTime, setCurrentTime] = React.useState(0)
  const [seekTime, setSeekTime] = React.useState(null)
  const moments = Array.isArray(contentQuery.data?.moments) ? contentQuery.data.moments : []
  const momentMutation = useMutation({
    mutationFn: payload => rest(`/content/${encodeURIComponent(video.name)}/moments`, { method: 'POST', body: payload }),
    onSuccess: () => {
      setNote('')
      setTimecode('')
      setValidationError('')
      queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'content', video.name] })
    },
  })

  function submitMoment(event) {
    event.preventDefault()
    const timeSeconds = parseTimecode(timecode)
    if (timeSeconds === null) {
      setValidationError('Entre un timecode valide, par exemple 02:15 ou 00:02:15.250.')
      return
    }
    if (!note.trim()) {
      setValidationError('Ajoute une note pour expliquer ce repère.')
      return
    }
    setValidationError('')
    momentMutation.mutate({ timeSeconds, note: note.trim(), targetFormat })
  }

  return jsxs('div', {
    className: 'flex min-h-0 flex-col gap-4',
    children: [
      jsx(PlaybackPanel, { rest, folderId: currentFolderId, video, onTimeUpdate: setCurrentTime, seekTime }),
      jsxs('form', {
        className: 'grid grid-cols-1 gap-3 md:grid-cols-[10rem_minmax(0,1fr)_9rem_auto]',
        onSubmit: submitMoment,
        children: [
          jsx('div', { className: 'flex flex-col gap-1', children: [jsx('label', { className: 'text-xs text-(--ui-text-secondary)', htmlFor: 'agent-editor-timecode', children: 'Timecode' }), jsx('input', { id: 'agent-editor-timecode', className: fieldClass(), value: timecode, onChange: event => setTimecode(event.target.value), placeholder: '00:32 ou 00:01:12', inputMode: 'text', autoComplete: 'off' }), jsx(Button, { type: 'button', onClick: () => setTimecode(formatTimecode(currentTime)), children: 'Prendre la position' })] }),
          jsx('div', { className: 'flex flex-col gap-1', children: [jsx('label', { className: 'text-xs text-(--ui-text-secondary)', htmlFor: 'agent-editor-note', children: 'À retenir' }), jsx('input', { id: 'agent-editor-note', className: fieldClass(), value: note, onChange: event => setNote(event.target.value), maxLength: 500, placeholder: 'Ex. réaction forte, accroche, payoff…' })] }),
          jsx('div', { className: 'flex flex-col gap-1', children: [jsx('label', { className: 'text-xs text-(--ui-text-secondary)', htmlFor: 'agent-editor-target', children: 'Cible' }), jsx('select', { id: 'agent-editor-target', className: fieldClass(), value: targetFormat, onChange: event => setTargetFormat(event.target.value), children: [jsx('option', { value: 'both', children: 'Les deux' }), jsx('option', { value: 'short', children: 'Short' }), jsx('option', { value: 'video', children: 'Vidéo' })] })] }),
          jsx('div', { className: 'flex items-end', children: jsx(Button, { type: 'submit', disabled: momentMutation.isPending, children: momentMutation.isPending ? 'Enregistrement…' : 'Ajouter le repère' }) }),
        ],
      }),
      validationError || momentMutation.error ? jsx('p', { className: 'text-sm text-(--ui-text-secondary)', role: 'alert', children: validationError || errorText(momentMutation.error, 'Le repère n’a pas pu être enregistré.') }) : null,
      jsxs(Panel, {
        title: `Repères · ${moments.length}`,
        description: 'Ils sont enregistrés avec cette vidéo et conservés localement.',
        className: 'flex-1',
        children: contentQuery.isLoading
          ? jsx('p', { className: 'py-4 text-sm text-(--ui-text-tertiary)', role: 'status', children: 'Chargement des repères…' })
          : contentQuery.error
            ? jsxs('div', {
              className: 'flex flex-col gap-3 py-4 text-sm',
              children: [jsx('p', { className: 'text-(--ui-text-secondary)', role: 'alert', children: errorText(contentQuery.error, 'Impossible de charger les repères.') }), jsx(Button, { type: 'button', onClick: () => contentQuery.refetch(), children: 'Réessayer' })],
            })
            : moments.length ? jsx('ol', {
              className: 'flex min-h-0 flex-col gap-2 overflow-auto',
              children: [...moments].sort((left, right) => left.timeSeconds - right.timeSeconds).map(moment => jsxs('li', {
                className: 'grid grid-cols-[7.5rem_minmax(0,1fr)_5rem] gap-3 border-b border-(--ui-stroke-secondary) py-2 text-sm',
                children: [jsx('button', { type: 'button', className: 'font-mono text-(--ui-accent) underline-offset-4 hover:underline', onClick: () => setSeekTime(moment.timeSeconds), 'aria-label': `Aller à ${formatTimecode(moment.timeSeconds)}`, children: formatTimecode(moment.timeSeconds) }), jsx('span', { className: 'break-words text-foreground', children: moment.note }), jsx('span', { className: 'text-right text-xs text-(--ui-text-tertiary)', children: moment.targetFormat === 'both' ? 'Les deux' : moment.targetFormat === 'short' ? 'Short' : 'Vidéo' })],
              }, moment.id)),
            }) : jsx('p', { className: 'py-4 text-sm text-(--ui-text-tertiary)', children: 'Pas encore de repère pour cette vidéo.' }),
      }),
    ],
  })
}

function ReferenceLibrary({ rest, openExternal, localVideos, selectedVideo, referencesQuery }) {
  const queryClient = useQueryClient()
  const [url, setUrl] = React.useState('')
  const [note, setNote] = React.useState('')
  const referenceMutation = useMutation({
    mutationFn: payload => rest('/references', { method: 'POST', body: payload }),
    onSuccess: () => {
      setUrl('')
      setNote('')
      queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'references'] })
    },
  })
  const deleteMutation = useMutation({
    mutationFn: referenceId => rest(`/references/${encodeURIComponent(referenceId)}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'references'] }),
  })
  const downloadMutation = useMutation({
    mutationFn: referenceId => rest(`/references/${encodeURIComponent(referenceId)}/download`, { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [...QUERY_ROOT, 'references'] }),
  })
  const references = Array.isArray(referencesQuery.data?.references) ? referencesQuery.data.references : []

  function submitReference(event) {
    event.preventDefault()
    referenceMutation.mutate({ url: url.trim(), note: note.trim() })
  }

  function downloadStatus(reference) {
    if (reference.downloadStatus === 'queued') return 'Téléchargement en attente…'
    if (reference.downloadStatus === 'downloading') return 'Téléchargement en cours…'
    if (reference.downloadStatus === 'completed') return `Copie locale prête · ${reference.videoFile || 'MP4 dans References'}`
    if (reference.downloadStatus === 'failed') return reference.downloadError || 'Téléchargement échoué.'
    return 'Copie locale non téléchargée.'
  }

  return jsxs('div', {
    className: 'grid min-h-0 grid-cols-1 gap-4 xl:grid-cols-2',
    children: [
      jsxs(Panel, {
        title: `Liens et notes · ${references.length}`,
        description: 'Les liens autorisés sont copiés en MP4 dans References et restent lisibles dans la bibliothèque locale.',
        children: [
          jsx('form', {
            className: 'flex flex-col gap-3',
            onSubmit: submitReference,
            children: [
              jsx('label', { className: 'flex flex-col gap-1 text-xs text-(--ui-text-secondary)', htmlFor: 'agent-editor-reference-url', children: [jsx('span', { children: 'Lien YouTube ou TikTok' }), jsx('input', { id: 'agent-editor-reference-url', className: fieldClass(), type: 'url', value: url, onChange: event => setUrl(event.target.value), placeholder: 'https://youtu.be/…', required: true, maxLength: 2048 })] }),
              jsx('label', { className: 'flex flex-col gap-1 text-xs text-(--ui-text-secondary)', htmlFor: 'agent-editor-reference-note', children: [jsx('span', { children: 'Note (facultatif)' }), jsx('textarea', { id: 'agent-editor-reference-note', className: fieldClass(), value: note, onChange: event => setNote(event.target.value), placeholder: 'Style, rythme, idée à retenir…', rows: 3, maxLength: 2000 })] }),
              jsx('button', { type: 'submit', className: buttonClass(true), disabled: referenceMutation.isPending || !url.trim(), children: referenceMutation.isPending ? 'Enregistrement…' : 'Ajouter la référence' }),
            ],
          }),
          referenceMutation.error ? jsx('p', { className: 'text-sm text-(--ui-text-secondary)', role: 'alert', children: errorText(referenceMutation.error, 'La référence n’a pas pu être enregistrée.') }) : null,
          referencesQuery.isLoading ? jsx('p', { className: 'text-sm text-(--ui-text-tertiary)', children: 'Chargement…' }) : null,
          referencesQuery.error ? jsx('p', { className: 'text-sm text-(--ui-text-secondary)', role: 'alert', children: errorText(referencesQuery.error, 'Impossible de charger les références.') }) : null,
          references.length ? jsx('ul', {
            className: 'flex max-h-[50vh] flex-col gap-2 overflow-auto',
            children: references.map(reference => jsxs('li', {
              className: 'flex flex-col gap-2 rounded-md border border-(--ui-stroke-secondary) p-3',
              children: [
                jsxs('div', { className: 'flex items-start justify-between gap-3', children: [jsx('button', { type: 'button', className: 'break-all text-left text-sm text-(--ui-accent) underline', onClick: () => openExternal(reference.url), children: reference.platform || 'Référence' }), jsx('button', { type: 'button', className: buttonClass(), onClick: () => { if (globalThis.confirm?.(reference.videoFile ? 'Retirer la référence et supprimer sa copie locale?' : 'Retirer cette référence de la bibliothèque?')) deleteMutation.mutate(reference.id) }, disabled: deleteMutation.isPending, children: 'Retirer' })] }),
                reference.note ? jsx('p', { className: 'whitespace-pre-wrap break-words text-sm text-(--ui-text-secondary)', children: reference.note }) : null,
                jsx('p', { className: 'text-xs text-(--ui-text-tertiary)', role: reference.downloadStatus === 'queued' || reference.downloadStatus === 'downloading' ? 'status' : undefined, children: downloadStatus(reference) }),
                ['not_requested', 'failed'].includes(reference.downloadStatus || 'not_requested')
                  ? jsx('button', { type: 'button', className: buttonClass(), onClick: () => downloadMutation.mutate(reference.id), disabled: downloadMutation.isPending, children: reference.downloadStatus === 'failed' ? 'Réessayer le téléchargement' : 'Télécharger la copie locale' })
                  : null,
              ],
            }, reference.id)),
          }) : !referencesQuery.isLoading && !referencesQuery.error ? jsx('p', { className: 'text-sm text-(--ui-text-tertiary)', children: 'Ajoute une référence pour la retrouver ici.' }) : null,
        ],
      }),
      jsx(Panel, {
        title: `Vidéo de référence · ${localVideos.length}`,
        description: selectedVideo?.name || 'Sélectionne un MP4 dans la bibliothèque pour le lire.',
        children: selectedVideo
          ? jsx(PlaybackPanel, { rest, folderId: 'references', video: selectedVideo })
          : jsx('p', { className: 'py-4 text-sm text-(--ui-text-tertiary)', children: 'Aucune vidéo MP4 locale à lire.' }),
      }),
    ],
  })
}

function AgentEditorPage({ rest, openExternal }) {
  const [activeFolder, setActiveFolder] = React.useState('waiting')
  const [selectedName, setSelectedName] = React.useState('')
  const [search, setSearch] = React.useState('')
  const videosQuery = useQuery({
    queryKey: [...QUERY_ROOT, 'videos'],
    queryFn: () => rest('/videos'),
    staleTime: 10000,
    refetchInterval: activeFolder === 'references' ? 3000 : false,
  })
  const referencesQuery = useQuery({
    queryKey: [...QUERY_ROOT, 'references'],
    queryFn: () => rest('/references'),
    staleTime: 10000,
    refetchInterval: activeFolder === 'references' ? 3000 : false,
  })
  const folders = Array.isArray(videosQuery.data?.folders) ? videosQuery.data.folders : []
  const currentFolder = folders.find(folder => folder.id === activeFolder)
  const videos = Array.isArray(currentFolder?.videos) ? currentFolder.videos : []
  const selectedVideo = videos.find(video => video.name === selectedName) || videos[0] || null
  const contentQuery = useQuery({
    queryKey: [...QUERY_ROOT, 'content', selectedVideo?.name || ''],
    queryFn: () => rest(`/content/${encodeURIComponent(selectedVideo.name)}`),
    enabled: activeFolder === 'waiting' && Boolean(selectedVideo),
    staleTime: 2000,
  })

  React.useEffect(() => {
    if (selectedVideo && selectedName !== selectedVideo.name) setSelectedName(selectedVideo.name)
    else if (!selectedVideo && selectedName) setSelectedName('')
  }, [activeFolder, selectedName, selectedVideo?.name])

  const folderTabs = [
    { id: 'waiting', label: 'À traiter' },
    { id: 'references', label: 'Références' },
    { id: 'finish', label: 'Terminées' },
  ]
  const localReferences = folders.find(folder => folder.id === 'references')
  const localReferenceVideos = Array.isArray(localReferences?.videos) ? localReferences.videos : []

  return jsxs('main', {
    className: 'flex h-full min-h-0 flex-col gap-4 p-4 text-sm',
    children: [
      jsxs('header', {
        className: 'flex flex-wrap items-start justify-between gap-3',
        children: [
          jsxs('div', { className: 'flex flex-col gap-1', children: [jsx('h1', { className: 'text-xl font-semibold text-foreground', children: 'Agent Editor' }), jsx('p', { className: 'text-sm text-(--ui-text-tertiary)', children: 'Vidéos, repères et références — sans quitter Hermes Desktop.' })] }),
          jsx('button', { type: 'button', className: buttonClass(), onClick: () => { videosQuery.refetch(); referencesQuery.refetch() }, children: 'Actualiser' }),
        ],
      }),
      videosQuery.error ? jsxs('div', { className: 'flex flex-wrap items-center justify-between gap-3 rounded-md border border-(--ui-stroke-secondary) p-3', role: 'alert', children: [jsx('span', { className: 'text-sm text-(--ui-text-secondary)', children: errorText(videosQuery.error, 'L’API video-agent ne répond pas. Vérifie que le backend du plugin est activé.') }), jsx('button', { type: 'button', className: buttonClass(), onClick: () => videosQuery.refetch(), children: 'Réessayer' })] }) : null,
      videosQuery.data && folders.length && folders.every(folder => !folder.available) ? jsx('div', { className: 'rounded-md border border-(--ui-stroke-secondary) p-3 text-sm text-(--ui-text-secondary)', role: 'status', children: 'Les dossiers Waiting, References et Finish ne sont pas tous disponibles. Vérifie le montage du workspace configuré.' }) : null,
      jsx('nav', { className: 'flex flex-wrap gap-2', role: 'tablist', 'aria-label': 'Dossiers vidéo', children: folderTabs.map(tab => {
        const folder = folders.find(item => item.id === tab.id)
        const count = folder?.count ?? 0
        return jsx('button', { type: 'button', role: 'tab', 'aria-selected': activeFolder === tab.id, className: buttonClass(activeFolder === tab.id), onClick: () => { setActiveFolder(tab.id); setSelectedName(''); setSearch('') }, children: `${tab.label} · ${count}` }, tab.id)
      }) }),
      videosQuery.isLoading ? jsx('div', { className: 'py-12 text-center text-sm text-(--ui-text-tertiary)', role: 'status', children: 'Chargement de la bibliothèque…' }) : null,
      !videosQuery.isLoading && currentFolder?.available === false && activeFolder !== 'references' ? jsx('div', { className: 'rounded-md border border-(--ui-stroke-secondary) p-6 text-sm text-(--ui-text-secondary)', children: `${currentFolder.label || 'Ce dossier'} n’est pas disponible dans le workspace actif.` }) : null,
      !videosQuery.isLoading && currentFolder && (currentFolder.available !== false || activeFolder === 'references') ? jsxs('div', {
        className: 'grid min-h-0 flex-1 grid-cols-1 gap-4 xl:grid-cols-3',
        children: [
          jsxs(Panel, {
            title: `${currentFolder?.label || 'Vidéos'} · ${videos.length}`,
            description: 'Les fichiers source sont en lecture seule.',
            className: 'min-h-[16rem] xl:col-span-1',
            children: [jsx('input', { className: fieldClass(), type: 'search', value: search, onChange: event => setSearch(event.target.value), placeholder: 'Filtrer les vidéos…', 'aria-label': 'Filtrer les vidéos' }), jsx(VideoList, { videos, selectedName: selectedVideo?.name || '', onSelect: setSelectedName, search })],
          }),
          activeFolder === 'references'
            ? jsx('div', { className: 'min-h-0 xl:col-span-2', children: jsx(ReferenceLibrary, { rest, openExternal, localVideos: localReferenceVideos, selectedVideo, referencesQuery }) })
            : activeFolder === 'waiting' && selectedVideo
              ? jsx(Panel, { title: selectedVideo.name, description: 'Workspace Waiting · lecture progressive', className: 'min-h-[16rem] xl:col-span-2', children: jsx(MomentWorkspace, { rest, video: selectedVideo, contentQuery, currentFolderId: activeFolder }) })
              : jsx(Panel, {
                title: selectedVideo ? selectedVideo.name : 'Aucune vidéo sélectionnée',
                description: 'Workspace Finish · lecture seule',
                className: 'min-h-[16rem] xl:col-span-2',
                children: selectedVideo
                  ? jsx(PlaybackPanel, { rest, folderId: activeFolder, video: selectedVideo })
                  : jsx('p', { className: 'py-4 text-sm text-(--ui-text-tertiary)', children: 'Ajoute des vidéos MP4 dans Finish pour les retrouver ici.' }),
              }),
        ],
      }) : null,
    ],
  })
}

export default {
  id: PLUGIN_ID,
  name: 'Agent Editor',
  register(ctx) {
    const rest = (path, options) => ctx.rest(path, options)
    const openExternal = url => ctx.os.openExternal(url)
    ctx.register({
      id: 'agent-editor-page',
      area: ROUTES_AREA,
      data: { path: '/agent-editor' },
      render: () => jsx(AgentEditorPage, { rest, openExternal }),
    })
    ctx.register({
      id: 'agent-editor-sidebar',
      area: SIDEBAR_NAV_AREA,
      data: { path: '/agent-editor', label: 'Agent Editor', codicon: 'play' },
    })
  },
}
