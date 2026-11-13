/**
 * 极简 ZIP（store，不压缩）打包器。
 *
 * 用途：对象分析的三份产物必须放在同一个「时间戳文件夹」里下载；
 * 浏览器不能直接建目录，所以打包成 zip，解压后自然得到上层文件夹。
 *
 * 只依赖 Uint8Array，不引第三方库；文件都是文本/小图，不做压缩换体积。
 */

export interface ZipFile {
  /** 相对路径，目录以 / 结尾；不允许绝对路径或 ..。 */
  name: string
  data: Uint8Array
}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256)
  for (let index = 0; index < 256; index += 1) {
    let value = index
    for (let bit = 0; bit < 8; bit += 1) {
      value = (value & 1) === 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1
    }
    table[index] = value >>> 0
  }
  return table
})()

function crc32(data: Uint8Array): number {
  let crc = 0xffffffff
  for (let index = 0; index < data.length; index += 1) {
    crc = (CRC_TABLE[(crc ^ data[index]!) & 0xff]! ^ (crc >>> 8)) >>> 0
  }
  return (crc ^ 0xffffffff) >>> 0
}

function dosDateTime(date: Date): { time: number; date: number } {
  const time = ((date.getHours() & 0x1f) << 11) | ((date.getMinutes() & 0x3f) << 5) | ((date.getSeconds() >> 1) & 0x1f)
  const day = ((date.getFullYear() - 1980) & 0x7f) << 9
  const month = ((date.getMonth() + 1) & 0x0f) << 5
  return { time, date: day | month | (date.getDate() & 0x1f) }
}

function assertSafe(name: string): void {
  if (name.startsWith('/') || name.includes('..') || name.includes('\\')) {
    throw new Error(`ZIP 路径不安全：${name}`)
  }
}

/** 生成一个 store 方式（method=0）的 zip。目录项会自动补上，确保解压后有上层文件夹。 */
export function zipStore(files: ZipFile[], date: Date = new Date()): Uint8Array {
  const encoder = new TextEncoder()
  const { time, date: dosDate } = dosDateTime(date)
  const entries = files.map((file) => {
    assertSafe(file.name)
    const name = encoder.encode(file.name)
    const crc = crc32(file.data)
    return { name, data: file.data, crc }
  })

  let total = 22
  for (const entry of entries) {
    total += 30 + entry.name.length + entry.data.length
    total += 46 + entry.name.length
  }

  const output = new Uint8Array(total)
  const view = new DataView(output.buffer)
  let offset = 0
  const central: Array<{ name: Uint8Array; crc: number; size: number; localOffset: number }> = []

  for (const entry of entries) {
    const localOffset = offset
    view.setUint32(offset, 0x04034b50, true)
    view.setUint16(offset + 4, 20, true)
    view.setUint16(offset + 6, 0x0800, true)
    view.setUint16(offset + 8, 0, true)
    view.setUint16(offset + 10, time, true)
    view.setUint16(offset + 12, dosDate, true)
    view.setUint32(offset + 14, entry.crc, true)
    view.setUint32(offset + 18, entry.data.length, true)
    view.setUint32(offset + 22, entry.data.length, true)
    view.setUint16(offset + 26, entry.name.length, true)
    view.setUint16(offset + 28, 0, true)
    offset += 30
    output.set(entry.name, offset)
    offset += entry.name.length
    output.set(entry.data, offset)
    offset += entry.data.length
    central.push({ name: entry.name, crc: entry.crc, size: entry.data.length, localOffset })
  }

  const centralStart = offset
  for (const entry of central) {
    view.setUint32(offset, 0x02014b50, true)
    view.setUint16(offset + 4, 20, true)
    view.setUint16(offset + 6, 20, true)
    view.setUint16(offset + 8, 0x0800, true)
    view.setUint16(offset + 10, 0, true)
    view.setUint16(offset + 12, time, true)
    view.setUint16(offset + 14, dosDate, true)
    view.setUint32(offset + 16, entry.crc, true)
    view.setUint32(offset + 20, entry.size, true)
    view.setUint32(offset + 24, entry.size, true)
    view.setUint16(offset + 28, entry.name.length, true)
    view.setUint16(offset + 30, 0, true)
    view.setUint16(offset + 32, 0, true)
    view.setUint16(offset + 34, 0, true)
    view.setUint16(offset + 36, 0, true)
    view.setUint32(offset + 38, 0, true)
    view.setUint32(offset + 42, entry.localOffset, true)
    offset += 46
    output.set(entry.name, offset)
    offset += entry.name.length
  }
  const centralSize = offset - centralStart

  view.setUint32(offset, 0x06054b50, true)
  view.setUint16(offset + 4, 0, true)
  view.setUint16(offset + 6, 0, true)
  view.setUint16(offset + 8, central.length, true)
  view.setUint16(offset + 10, central.length, true)
  view.setUint32(offset + 12, centralSize, true)
  view.setUint32(offset + 16, centralStart, true)
  view.setUint16(offset + 20, 0, true)
  return output
}
