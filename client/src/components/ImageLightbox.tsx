import { useEffect } from 'react'

/** 全屏图片预览：点击遮罩或按 Esc 关闭。 */
export default function ImageLightbox({
  src,
  caption,
  onClose,
}: {
  src: string
  caption?: string
  onClose: () => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/85 p-6"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <img
        src={src}
        alt={caption ?? '预览'}
        className="max-h-[85vh] max-w-full rounded-lg object-contain shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      />
      {caption && (
        <p className="mt-3 max-w-full truncate text-sm text-white/80">{caption}</p>
      )}
      <button
        type="button"
        className="absolute right-4 top-4 rounded-full bg-white/10 px-3 py-1.5 text-sm text-white hover:bg-white/20"
        onClick={onClose}
      >
        关闭 (Esc)
      </button>
    </div>
  )
}
