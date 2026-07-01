import { useEffect, useRef, useState } from 'react'

// Respects prefers-reduced-motion — if the user (or their OS) has asked
// for less motion, jump straight to the final value instead of animating.
const prefersReducedMotion =
  typeof window !== 'undefined' &&
  window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

export function useCountUp(target, { duration = 600 } = {}) {
  const [value, setValue] = useState(prefersReducedMotion ? target : 0)
  const frameRef = useRef()
  const startRef = useRef()
  const fromRef = useRef(0)

  useEffect(() => {
    if (target == null || Number.isNaN(target)) return

    if (prefersReducedMotion) {
      setValue(target)
      return
    }

    fromRef.current = value
    startRef.current = null

    function step(timestamp) {
      if (startRef.current === null) startRef.current = timestamp
      const elapsed = timestamp - startRef.current
      const progress = Math.min(elapsed / duration, 1)
      // ease-out cubic — quick start, gentle settle, not a linear tick
      const eased = 1 - Math.pow(1 - progress, 3)
      const next = fromRef.current + (target - fromRef.current) * eased
      setValue(next)
      if (progress < 1) {
        frameRef.current = requestAnimationFrame(step)
      }
    }

    frameRef.current = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frameRef.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, duration])

  return value
}
