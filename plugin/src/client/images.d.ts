/**
 * 静态资源的模块声明。
 *
 * `build.mjs` 用 esbuild 的 `dataurl` loader 把图片内联进 `lib/client.js`，
 * 所以运行时拿到的是 data URI 字符串；这里只为编辑器/类型检查补上形状。
 */
declare module '*.png' {
  const dataUrl: string
  export default dataUrl
}

declare module '*.svg' {
  const dataUrl: string
  export default dataUrl
}
