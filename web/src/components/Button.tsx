import type { ButtonHTMLAttributes } from 'react'

type Variant = 'primary' | 'success' | 'ghost'

// Tailwind v4 scans source as plain text, so variant classes must be written
// out in a lookup — `bg-${variant}` compiles to nothing.
const VARIANTS: Record<Variant, string> = {
  primary: 'bg-accent-strong text-ink hover:bg-accent hover:text-canvas',
  success: 'bg-esc-calm text-canvas hover:brightness-110',
  ghost: 'border border-edge bg-surface-raised text-ink hover:border-accent',
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
}

function Button({
  variant = 'primary',
  className = '',
  ...props
}: ButtonProps) {
  return (
    <button
      {...props}
      className={`cursor-pointer rounded-full px-8 py-3 text-sm font-semibold tracking-wide uppercase transition focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-canvas focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 ${VARIANTS[variant]} ${className}`}
    />
  )
}

export default Button
