// 产物面板的纯解析契约（Node 直跑，不需要浏览器）。
//
// CSV 是新增的预览类型：它的失败形态很安静——引号里的逗号被拆错、换行被吃掉，
// 表格看起来"有内容"但每列都错位。这里直接驱动 artifact-preview.js 的 parseCsv。
//
// 用法：node tests/js/artifact_parser.mjs

import { parseCsv } from "../../workbench/static/js/artifact-preview.js";

const cases = {
  simple: parseCsv("a,b,c\n1,2,3\n"),
  quotedComma: parseCsv('name,note\n"a,b","他说 ""好"""\n'),
  crlf: parseCsv("h1,h2\r\nv1,v2\r\n"),
  empty: parseCsv(""),
};

console.log(JSON.stringify(cases));
