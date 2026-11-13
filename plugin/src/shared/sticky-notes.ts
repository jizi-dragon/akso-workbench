/**
 * 生命周期 / 工作流 drawio 的固定图形库。
 *
 * 唯一来源：`docs/analysis/便笺本.xml`（用户提供的便笺本，禁止另起配色）。
 * 生成器只允许从这里取模块样式；新增图形先补便笺本，再补这里。
 *
 * 颜色与形状对应：
 *   生命周期状态  #bdd7ee 矩形
 *   用户动作      #ed7d31 胶囊
 *   用户任务      #ed7d31 矩形
 *   通知          #fee599 矩形
 *   判断          #f4b183 菱形
 *   工作流（开始/结束） #70ad47 圆形
 *   泳道图        透明大框
 */
export const STICKY_NOTE_LIBRARY = 'docs/analysis/便笺本.xml'

export const STICKY_STYLES = {
  lifecycleStatus:
    'rounded=0;whiteSpace=wrap;html=1;fillColor=#bdd7ee;strokeColor=#939393;',
  userAction:
    'rounded=1;arcSize=50;whiteSpace=wrap;html=1;fillColor=#ed7d31;strokeColor=#939393;',
  userTask:
    'rounded=0;whiteSpace=wrap;html=1;fillColor=#ed7d31;strokeColor=#939393;',
  notification:
    'rounded=0;whiteSpace=wrap;html=1;fillColor=#fee599;strokeColor=#939393;',
  decision:
    'rhombus;whiteSpace=wrap;html=1;fillColor=#f4b183;strokeColor=#939393;',
  startEnd:
    'ellipse;whiteSpace=wrap;html=1;fillColor=#70ad47;strokeColor=#939393;',
  swimlane:
    'rounded=0;whiteSpace=wrap;html=1;fillColor=none;strokeColor=#939393;',
  edge:
    'edgeStyle=orthogonalEdgeStyle;html=1;endArrow=block;strokeColor=#000000;',
} as const
