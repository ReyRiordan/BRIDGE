import { useState } from 'react'

interface SceneLayerProps {
  /** Action `type`, or `__patient__`. Exposed as `data-layer` for tests. */
  layerKey: string
  src: string | null
  z: number
}

/**
 * One layer of the composite, cross-faded mount-and-keep.
 *
 * Every src this layer has ever shown stays mounted; only opacity changes. That
 * is what fixes the legacy fade-through-empty flicker — swapping a single
 * <img>'s src meant the browser tore down the old frame before the new one had
 * decoded, so the layer blinked through transparent mid-transition.
 *
 * `seen` grows with a render-phase setState (React's documented "adjust state
 * when props change" pattern): React re-runs this component before committing,
 * so the new frame is mounted in the same commit that reveals it — an effect
 * would add it one commit late and flash.
 *
 * The wrapper div (not the images) is the assertion surface: jsdom never loads
 * an image, so tests read `data-layer` / `data-src` / zIndex instead.
 */
function SceneLayer({ layerKey, src, z }: SceneLayerProps) {
  const [seen, setSeen] = useState<string[]>([])
  if (src !== null && !seen.includes(src)) {
    setSeen([...seen, src])
  }

  return (
    <div
      data-layer={layerKey}
      data-src={src ?? ''}
      className="pointer-events-none absolute inset-0"
      style={{ zIndex: z }}
    >
      {seen.map((source) => (
        <img
          key={source}
          src={source}
          alt=""
          aria-hidden="true"
          // A null src fades every frame out — Force IV and Restraint have no
          // inactive art at all.
          className={`absolute inset-0 h-full w-full object-cover motion-safe:transition-opacity motion-safe:duration-300 ${source === src ? 'opacity-100' : 'opacity-0'}`}
        />
      ))}
    </div>
  )
}

export default SceneLayer
