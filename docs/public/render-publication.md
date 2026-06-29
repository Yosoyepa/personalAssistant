# Render y publicacion de artefactos publicos

Esta guia cubre el render del video publico y el checklist previo a publicar en
LinkedIn o GitHub. No requiere secretos y no debe usar archivos `.env`,
`.env.local`, tokens, IDs reales de usuarios, URLs privadas ni trazas internas.

## Artefacto esperado

- Proyecto Remotion: `media/remotion/`
- Video final: `media/remotion/out/personal-assistant-architecture.mp4`
- Duracion esperada del video: 24 segundos, calculada desde 720 frames a 30 FPS
- Tiempo esperado de render local: 1 a 5 minutos, segun CPU/GPU, resolucion y
  assets
- Formato recomendado: MP4 H.264, 1080p, 30 FPS

Si el guion cambia, mantener el video por debajo de 60 segundos para LinkedIn y
para que el README/issue de GitHub siga siendo facil de revisar. Si cambia
`durationInFrames` o `fps`, actualizar esta guia junto con el cambio de video.

## Comandos npm

Ejecutar todo desde `media/remotion/`:

```bash
cd media/remotion
npm install
npm run preview
npm run render
```

El script `npm run preview` debe abrir el preview de Remotion para revisar
composicion, textos y assets antes de renderizar. El script `npm run render`
debe generar:

```text
media/remotion/out/personal-assistant-architecture.mp4
```

Si se necesita limpiar un render anterior:

```bash
cd media/remotion
rm -rf out
npm run render
```

## Validacion local del MP4

Antes de publicar:

```bash
test -f media/remotion/out/personal-assistant-architecture.mp4
ls -lh media/remotion/out/personal-assistant-architecture.mp4
```

Revisar manualmente el MP4 completo:

- El video reproduce de inicio a fin sin cuadros negros inesperados.
- La duracion esperada es de 24 segundos.
- Los textos son legibles en pantalla movil.
- No hay secretos, tokens, endpoints internos, correos privados, telefonos,
  tenant IDs reales ni capturas de conversaciones reales.
- No aparecen rutas locales absolutas, `.env`, logs de debug ni informacion de
  infraestructura privada.
- Audio, si existe, no contiene datos sensibles y tiene volumen consistente.
- El cierre incluye una llamada publica apropiada, sin promesas tecnicas no
  verificadas.

## Checklist LinkedIn

Publicar solo despues de pasar la validacion local.

- Usar `media/remotion/out/personal-assistant-architecture.mp4` como video
  adjunto.
- Mantener el copy publico: problema, enfoque, resultado y aprendizaje.
- Evitar credenciales, nombres de clientes, capturas privadas o metricas no
  verificables.
- Confirmar que el thumbnail no expone informacion sensible.
- Confirmar que el primer segundo comunica claramente el producto o demo.
- Agregar alt text o descripcion breve del contenido del video.
- No publicar `.env`, logs, trazas, prompts privados ni conversaciones reales.
- Revisar que los links apunten a recursos publicos y estables.

## Checklist GitHub

Para publicar en GitHub, preferir un release, issue o PR description con el MP4
adjunto si el repositorio no debe almacenar binarios grandes.

- Adjuntar `media/remotion/out/personal-assistant-architecture.mp4` o enlazarlo
  desde un release publico.
- No commitear secretos ni archivos `.env`.
- No subir datos reales de usuarios, capturas privadas ni trazas internas.
- Verificar que el archivo MP4 no sea innecesariamente grande para el canal de
  publicacion.
- Incluir contexto minimo: objetivo de la demo, duracion, fecha de render y hash
  del commit si aplica.
- Revisar que la descripcion no prometa integraciones o capacidades fuera del
  estado real del proyecto.
- Si el MP4 se agrega al repo, confirmar primero que la politica de binarios del
  proyecto lo permite.

## Plantilla corta de publicacion

```text
Demo publica del asistente personal: flujo conversacional, extraccion de tareas
y recordatorios con arquitectura modular.

Duracion: 24s
Video: media/remotion/out/personal-assistant-architecture.mp4
Sin secretos ni datos reales de usuarios.
```
