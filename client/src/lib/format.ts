export function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds))
  const m = Math.floor(s / 60)
  const r = s % 60
  return m > 0 ? `${m}m${r.toString().padStart(2, '0')}s` : `${r}s`
}


/**
 * 同一段内两个镜头认领同一拍时（同景别切换，如"全景建立 + 切特写"），
 * 只播一次节拍原文，后续镜头显示"与上一镜同属一拍 + 景别"，避免读成内容重复。
 */
export function dedupeShotBeats(
  beatsList: string[][] | undefined,
  cameras: string[],
): { text: string | null; sameAsPrev: boolean; camera: string }[] {
  const list = beatsList ?? []
  return list.map((beats, i) => {
    const prev = i > 0 ? list[i - 1] : null
    // 本镜"没有新增节拍"（所认领的节拍已被上一镜全部覆盖）也算同拍：
    // 例如上一镜认领 [1,7]、本镜只认领 [1]（切特写），内容并未推进
    const same =
      Boolean(prev && prev.length > 0 && beats.length > 0) &&
      beats.every((beat) => prev!.includes(beat))
    return { text: same ? null : beats.join('；'), sameAsPrev: same, camera: cameras[i] ?? '' }
  })
}
