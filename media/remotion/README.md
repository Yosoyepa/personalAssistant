# Remotion public demo

Esta carpeta contiene el proyecto Remotion para renderizar el video publico del
asistente personal. El render final esperado es:

```text
media/remotion/out/personal-assistant-architecture.mp4
```

## Requisitos

- Node.js y npm disponibles en la maquina local.
- Dependencias instaladas con `npm install` dentro de `media/remotion/`.
- Assets publicos solamente: no usar `.env`, tokens, capturas privadas, logs
  internos ni datos reales de usuarios.

## Scripts esperados

El `package.json` de esta carpeta debe exponer estos scripts:

```json
{
  "scripts": {
    "preview": "remotion preview src/Root.tsx",
    "render": "mkdir -p out && remotion render src/Root.tsx PersonalAssistantArchitecture out/personal-assistant-architecture.mp4"
  }
}
```

La composicion principal esperada se llama `PersonalAssistantArchitecture`. Si
el nombre real cambia, actualizar el script `render` y esta documentacion en el
mismo cambio.

## Render local

Desde la raiz del repositorio:

```bash
cd media/remotion
npm install
npm run preview
npm run render
```

`npm run preview` se usa para revisar la composicion antes de exportar.
`npm run render` debe crear:

```text
media/remotion/out/personal-assistant-architecture.mp4
```

Para regenerar desde cero:

```bash
cd media/remotion
rm -rf out
npm run render
```

## Duracion esperada

- Video: 24 segundos, calculado desde 720 frames a 30 FPS.
- Render local: 1 a 5 minutos, segun hardware, resolucion, FPS y assets.
- Perfil recomendado: MP4 H.264, 1080p, 30 FPS.

## Checklist previo a publicar

- El MP4 existe en `media/remotion/out/personal-assistant-architecture.mp4`.
- El video reproduce completo y dura 24 segundos.
- Textos y UI se leen en desktop y movil.
- No hay secretos, tokens, endpoints privados, IDs reales ni `.env`.
- No hay datos reales de usuarios, capturas privadas, trazas internas ni logs de
  debug.
- El thumbnail y el primer segundo son publicos y comprensibles.
- El copy de LinkedIn/GitHub no promete capacidades que el proyecto todavia no
  tenga.

Ver tambien `docs/public/render-publication.md` para el checklist de publicacion
en LinkedIn y GitHub.
